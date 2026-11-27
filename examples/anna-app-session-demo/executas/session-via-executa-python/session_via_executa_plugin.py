#!/usr/bin/env python3
"""session_via_executa_plugin.py — the Reverse RPC half of the Agent Session Demo.

    page ── anna.tools.invoke ──▶ this Executa ── agent/session.* ──▶ host

One tool, ``agent_session``. Its ``op`` argument selects one of the eleven
session interfaces, and every op is one call on the public interface of
``executa_sdk.AgentSessionClient`` / ``AgentSession`` — the same interfaces
the page reaches directly through ``anna.agent.session.*``:

    op          SDK call                                  reverse RPC
    ──────────  ────────────────────────────────────────  ────────────────────────
    create      client.create(...)                        agent/session.create
    attach      client.attach(uuid)                       agent/session.refresh
    list        client.list(...)                          agent/session.list
    refresh     client.refresh(uuid)                      agent/session.refresh
    run         (await client.attach(uuid)).run(...)      agent/session.run
    cancel      (await client.attach(uuid)).cancel(id)    agent/session.cancel
    history     (await client.attach(uuid)).history()     agent/session.history
    delete      (await client.attach(uuid)).delete()      agent/session.delete
    run_status  client.run_status(uuid, run_id)           agent/session.run.status
    frames      client.frames(stream_id, ...)             agent/session.frames
    catalog     client.catalog()                          agent/session.catalog

The plugin keeps no session state: the caller threads the
``app_session_uuid`` through every invoke, and the ops that need a handle
``attach`` first. ``attach`` refreshes, which makes the host obtain and
cache the session's ticket — so it works for a session this plugin
created, one it created before a restart, and one created by the App's
page (inside an App the page and its Executas share sessions).

Answers are ``{"op", "ok": true, "result": <what the SDK returned>}``. A
session-level failure (``AgentError`` — session not found, quota, provider
error …) is an answer too: ``{"op", "ok": false, "error": {code, name,
message}}``. Reporting it in-band keeps the code and name intact on every
host; a JSON-RPC error would reach the page as an opaque ``tool_failed``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Fallback for fresh checkouts: locate the in-repo SDK when not pip-installed.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[4] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    PROTOCOL_VERSION_V2,
    AgentError,
    AgentSession,
    AgentSessionClient,
    bind_invoke,
)

# ─── Manifest ────────────────────────────────────────────────────────
# The host keys this plugin by the server-assigned tool_id, not by a name
# declared here (see llm-via-executa for the full note).

OPS = (
    "create",
    "attach",
    "list",
    "refresh",
    "run",
    "cancel",
    "history",
    "delete",
    "run_status",
    "frames",
    "catalog",
)

MANIFEST = {
    "display_name": "Session via Executa",
    "version": "0.1.0",
    "description": (
        "Drives every agent/session.* reverse RPC on behalf of the Agent "
        "Session Demo app, so the page can run the same session code over "
        "the Host API and over Reverse RPC."
    ),
    "author": "Anna Developer",
    "tools": [
        {
            "name": "agent_session",
            # op=run holds this invoke for the whole agent turn. The host
            # honors this per-tool deadline when the caller does not
            # negotiate one (the default would be 60s).
            "timeout": 180,
            "description": (
                "One Anna App Session interface per call, selected by 'op' "
                "(create|attach|list|refresh|run|cancel|history|delete|"
                "run_status|frames|catalog). Mirrors the page's "
                "anna.agent.session.* surface over agent/session.* "
                "reverse RPC. Stateless: pass 'app_session_uuid' to every "
                "op that targets a session."
            ),
            "parameters": [
                {
                    "name": "op",
                    "type": "string",
                    "description": "Session interface to call.",
                    "required": True,
                    "enum": list(OPS),
                },
                {
                    "name": "app_session_uuid",
                    "type": "string",
                    "description": (
                        "Target session. Required for attach / refresh / "
                        "run / cancel / history / delete / run_status."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "submode",
                    "type": "string",
                    "description": "op=create: agent submode (auto|fixed).",
                    "required": False,
                    "default": "auto",
                },
                {
                    "name": "fixed_client_id",
                    "type": "string",
                    "description": (
                        "op=create with submode=fixed: the client_id every "
                        "run of the session is pinned to."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "system_prompt",
                    "type": "string",
                    "description": (
                        "op=create: session-level system prompt, applied "
                        "to every run of the session."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "label",
                    "type": "string",
                    "description": "op=create: human-friendly session label.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "content",
                    "type": "string",
                    "description": "op=run: the user turn.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "run_id",
                    "type": "string",
                    "description": (
                        "op=run: optional caller-chosen run id — choose it "
                        "up front to be able to cancel the run while this "
                        "invoke is still in flight. Required for cancel / "
                        "run_status."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "stream_id",
                    "type": "string",
                    "description": (
                        "op=frames: the archived stream to replay. Take it "
                        "from op=run_status — the stream_id inside a run "
                        "answer is host-local."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "after_seq",
                    "type": "integer",
                    "description": "op=frames: return frames with seq greater than this.",
                    "required": False,
                    "default": 0,
                },
                {
                    "name": "limit",
                    "type": "integer",
                    "description": (
                        "op=list: max sessions (1-100, default 50). "
                        "op=frames: max frames per page (1-500, default 500)."
                    ),
                    "required": False,
                    "default": None,
                },
                {
                    "name": "include_expired",
                    "type": "boolean",
                    "description": "op=list: include expired sessions.",
                    "required": False,
                    "default": False,
                },
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ─── stdio plumbing ──────────────────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


agent_client = AgentSessionClient(write_frame=_write_frame)


# ─── Tool implementation ─────────────────────────────────────────────


def _handle_info(sess: AgentSession) -> dict:
    return {
        "app_session_uuid": sess.uuid,
        "kind": sess.kind,
        "submode": sess.agent_submode,
        "fixed_client_id": sess.fixed_client_id,
        "expires_in": sess.expires_in,
    }


def _error_info(e: AgentError) -> dict:
    data = e.data if isinstance(e.data, dict) else {}
    return {"code": e.code, "name": data.get("errorCode"), "message": e.message}


async def _agent_session(
    op: str,
    *,
    app_session_uuid: str = "",
    submode: str = "auto",
    fixed_client_id: str = "",
    system_prompt: str = "",
    label: str = "",
    content: str = "",
    run_id: str = "",
    stream_id: str = "",
    after_seq: int = 0,
    limit: int | None = None,
    include_expired: bool = False,
) -> dict:
    if op not in OPS:
        raise ValueError(f"unknown op={op!r}; expected one of {', '.join(OPS)}")

    # — interfaces that need no session —

    if op == "create":
        sess = await agent_client.create(
            agent_submode=submode or "auto",
            fixed_client_id=fixed_client_id or None,
            label=label or None,
            system_prompt=(system_prompt or "").strip() or None,
        )
        return {
            **_handle_info(sess),
            "granted_tools": sess.granted_tools,
            "inherit_host_tools": sess.inherit_host_tools,
        }

    if op == "list":
        sessions = await agent_client.list(
            include_expired=bool(include_expired), limit=int(limit or 50)
        )
        return {"sessions": sessions}

    if op == "catalog":
        return await agent_client.catalog()

    if op == "frames":
        if not stream_id:
            raise ValueError("op='frames' requires 'stream_id'")
        return await agent_client.frames(
            stream_id, after_seq=int(after_seq or 0), limit=int(limit or 500)
        )

    # — interfaces addressed by app_session_uuid —

    if not app_session_uuid:
        raise ValueError(f"op={op!r} requires 'app_session_uuid'")

    if op == "attach":
        # The refresh behind attach() does not describe the tool surface, so
        # none is reported here; the run_meta frame of the next run does.
        return _handle_info(await agent_client.attach(app_session_uuid))

    if op == "refresh":
        return await agent_client.refresh(app_session_uuid)

    if op == "run_status":
        if not run_id:
            raise ValueError("op='run_status' requires 'run_id'")
        return await agent_client.run_status(app_session_uuid, run_id)

    if op == "cancel" and not run_id:
        raise ValueError("op='cancel' requires 'run_id'")
    if op == "run" and not (content or "").strip():
        raise ValueError("op='run' requires 'content'")

    sess = await agent_client.attach(app_session_uuid)

    if op == "run":
        # Fix the run id here so the answer can name it; the caller needs it
        # for cancel / run_status.
        run_id = run_id or f"run_{uuid.uuid4().hex[:16]}"
        frames: list[dict] = []
        first_frame_ms: int | None = None
        t0 = time.perf_counter()
        # Frames arrive here while the run is in flight (the stdin loop feeds
        # agent/session.frame notifications to the SDK). Timing the first one
        # against the whole turn is the evidence the caller can see: a
        # tools.invoke answers once, so the frames travel back together.
        async for frame in sess.run(content, run_id=run_id):
            if first_frame_ms is None:
                first_frame_ms = round((time.perf_counter() - t0) * 1000)
            frames.append(frame)
        return {
            "run_id": run_id,
            "frames": frames,
            "first_frame_ms": first_frame_ms,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        }

    if op == "cancel":
        return await sess.cancel(run_id)

    if op == "history":
        return await sess.history()

    return await sess.delete()


# ─── JSON-RPC dispatch ───────────────────────────────────────────────


def _make_response(req_id, *, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _handle_initialize(req_id, params: dict) -> dict:
    proto = (params or {}).get("protocolVersion") or "1.1"
    if proto != PROTOCOL_VERSION_V2:
        agent_client.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "agent/* requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": (
                {"agent": {"submodes": ["auto", "fixed"]}}
                if proto == PROTOCOL_VERSION_V2
                else {}
            ),
            "capabilities": {},
        },
    )


def _handle_health(req_id) -> dict:
    return _make_response(
        req_id,
        result={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": MANIFEST["version"],
        },
    )


_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()


async def _invoke(req_id, params: dict) -> None:
    tool = params.get("tool")
    args = dict(params.get("arguments") or {})
    if tool != "agent_session":
        _write_frame(
            _make_response(req_id, error={"code": -32601, "message": f"Unknown tool: {tool}"})
        )
        return

    op = str(args.pop("op", "") or "").strip().lower()
    try:
        # Stamps context.invoke_id on every reverse RPC issued below, so the
        # host can tell concurrent invokes apart — a cancel typically arrives
        # while the run it targets is still in flight.
        with bind_invoke(params):
            data = {"op": op, "ok": True, "result": await _agent_session(op, **args)}
    except AgentError as e:
        data = {"op": op, "ok": False, "error": _error_info(e)}
    except (TypeError, ValueError) as e:
        _write_frame(
            _make_response(req_id, error={"code": -32602, "message": f"Invalid params: {e}"})
        )
        return
    except Exception as e:  # noqa: BLE001
        _write_frame(
            _make_response(
                req_id, error={"code": -32603, "message": f"Tool execution failed: {e}"}
            )
        )
        return
    _write_frame(_make_response(req_id, result={"success": True, "tool": tool, "data": data}))


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply from host → resolve the pending agent/* call.
    if "method" not in msg:
        if agent_client.dispatch_response(msg):
            return
        print(f"⚠️  unmatched response id={msg.get('id')!r}", file=sys.stderr)
        return
    # Live frame of an in-flight agent/session.run (host → plugin notification).
    elif agent_client.dispatch_notification(msg):
        return

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "invoke":
        # Runs on the asyncio loop and answers from there: this thread goes
        # straight back to stdin, so replies, live frames and further
        # invokes are never queued behind a run that is waiting for them.
        asyncio.run_coroutine_threadsafe(_invoke(req_id, params), _loop)
        return

    if method == "initialize":
        resp = _handle_initialize(req_id, params)
    elif method == "describe":
        resp = _make_response(req_id, result=MANIFEST)
    elif method == "health":
        resp = _handle_health(req_id)
    elif method == "shutdown":
        resp = _make_response(req_id, result={"ok": True})
    else:
        resp = _make_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    if req_id is not None:
        _write_frame(resp)


def main() -> None:
    print("🔌 session-via-executa plugin started", file=sys.stderr)
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if line:
                _handle_message(line)
    finally:
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
