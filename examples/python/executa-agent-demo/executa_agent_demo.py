#!/usr/bin/env python3
"""executa_agent_demo.py — Executa/App parity with `agent.session.*` reverse RPC.

This example brings stdio Executas to **full parity** with
anna-app iframes for LLM access:

* uses ``executa_sdk.AgentSessionClient`` to mint and run an
  Anna App Session entirely through reverse JSON-RPC;
* the executa **never** sees a bearer token — the Anna Agent caches
  the ``app_session_token`` internally, indexed by ``app_session_uuid``;
* the SDK's ``async for frame in session.run(...)`` API matches
  anna-app's ``llm.runAgent()`` so the same prompts work in both
  surfaces unchanged.

It exposes two tools:

* ``ask_agent``  — multi-turn agent run via ``agent/session.run``
* ``ask_complete`` — single-shot stateless completion via ``agent/complete``

End-to-end requirements:

1. Manifest declares ``host_capabilities: ["llm.sample",
   "llm.agent.auto"]`` (Anna Server refuses ``agent/*`` reverse-RPC
   otherwise).
2. The end user has toggled ``agent_grant.enabled = true`` for this
   Executa in their Anna Admin panel (the same panel that gates
   ``sampling_grant``).

Run locally: see ``README.md`` § "Run locally" — stdio probe for
``describe``, then register via ``uv tool install .`` (or build a
PyInstaller binary) and enroll in Anna Admin for the end-to-end
reverse-RPC path.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Allow running directly from a fresh checkout: fall back to in-repo SDK.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[3] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    AgentError,
    AgentSessionClient,
    PROTOCOL_VERSION_V2,
    SamplingClient,
    SamplingError,
)

# ─── Manifest ────────────────────────────────────────────────────────

MANIFEST = {
    "name": "executa-agent-demo",
    "display_name": "Executa Agent Demo",
    "version": "0.1.0",
    "description": "Demonstrates plugin/app parity for the Anna agent surface via agent.session.*",
    "author": "Anna Developer",
    # NEW in v2 — declare both sampling AND agent.auto so users see
    # exactly which host capabilities they are authorizing.
    "host_capabilities": ["llm.sample", "llm.agent.auto"],
    "tools": [
        {
            "name": "ask_agent",
            "description": "Mint a per-call Anna App Session and run one agent turn (multi-step, tool-using).",
            "parameters": [
                {"name": "question", "type": "string", "description": "User question for the agent.", "required": True},
                {"name": "label", "type": "string", "description": "Human-friendly label for the session.", "required": False, "default": None},
            ],
        },
        {
            "name": "ask_complete",
            "description": "Single-shot stateless LLM completion (L1) via agent/complete.",
            "parameters": [
                {"name": "prompt", "type": "string", "description": "Prompt to send.", "required": True},
                {"name": "max_tokens", "type": "integer", "description": "Max output tokens.", "required": False, "default": 256},
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ─── Frame I/O + clients ─────────────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# Two clients sharing the same writer; both multiplex over the same stdin loop.
sampling = SamplingClient(write_frame=_write_frame)
agent = AgentSessionClient(write_frame=_write_frame)


# ─── Tool implementations ───────────────────────────────────────────


async def _ask_agent(question: str, label: str | None = None) -> dict:
    """Mint a session, run one turn, drain frames, delete the session."""
    if not question.strip():
        return {"answer": "", "note": "empty question"}

    session = await agent.create(
        kind="agent",
        agent_submode="auto",
        label=label or "executa-agent-demo run",
        ttl_seconds=600,
    )
    try:
        deltas: list[str] = []
        final_text = ""
        async for frame in session.run(question, recursion_limit=8):
            ev = frame.get("event")
            # Accept several token-emitting event names — the matrix-nexus
            # SSE producer historically used "token" while the bridge
            # normalises to "delta"; harness fixtures also use "token".
            if ev in ("delta", "token", "message"):
                txt = frame.get("text") or ""
                if txt:
                    deltas.append(txt)
            elif ev == "final":
                # Some producers omit `text` on the terminal frame and only
                # emit usage; fall back to the accumulated deltas.
                final_text = (frame.get("text") or "").strip() or "".join(deltas)
            elif ev == "complete":
                # Sentinel-only terminator (no text); use accumulated deltas.
                if not final_text:
                    final_text = "".join(deltas)
        return {
            "answer": final_text or "".join(deltas),
            "session_uuid": session.uuid,
            "granted_tools": session.granted_tools,
        }
    finally:
        # Always tear down — token cache in the host is bounded but
        # leaving it grow unboundedly would consume dev-session quota.
        try:
            await session.delete()
        except AgentError:
            pass


async def _ask_complete(prompt: str, max_tokens: int = 256) -> dict:
    res = await agent.complete(
        messages=[{"role": "user", "content": {"type": "text", "text": prompt}}],
        max_tokens=int(max_tokens),
        system_prompt="You are a concise, friendly assistant.",
        metadata={"tool": "ask_complete"},
    )
    content = res.get("content") or {}
    text = content.get("text") if isinstance(content, dict) else ""
    return {
        "answer": text or "",
        "model": res.get("model"),
        "usage": res.get("usage"),
        "stopReason": res.get("stopReason"),
    }


# ─── JSON-RPC dispatch (mirrors sampling-summarizer skeleton) ───────


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
        sampling.disable(
            f"host did not negotiate v2 (got {proto!r}); agent/* needs Executa 2.0"
        )
        agent.disable(
            f"host did not negotiate v2 (got {proto!r}); agent/* needs Executa 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["name"], "version": MANIFEST["version"]},
            "client_capabilities": (
                {"sampling": {}, "agent": {"submodes": ["auto"]}}
                if proto == PROTOCOL_VERSION_V2
                else {}
            ),
            "capabilities": {},
        },
    )


def _handle_describe(req_id) -> dict:
    return _make_response(req_id, result=MANIFEST)


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


def _handle_invoke(req_id, params: dict) -> dict:
    tool = params.get("tool")
    args = params.get("arguments") or {}

    if tool == "ask_agent":
        coro = _ask_agent(**args)
    elif tool == "ask_complete":
        coro = _ask_complete(**args)
    else:
        return _make_response(req_id, error={"code": -32601, "message": f"Unknown tool: {tool}"})

    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        data = fut.result(timeout=180.0)
    except (AgentError, SamplingError) as e:
        return _make_response(
            req_id,
            error={"code": e.code, "message": e.message, "data": e.data},
        )
    except Exception as e:  # noqa: BLE001
        return _make_response(
            req_id,
            error={"code": -32603, "message": f"Tool execution failed: {e}"},
        )
    return _make_response(req_id, result={"success": True, "tool": tool, "data": data})


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply from host → try BOTH clients in turn.
    if "method" not in msg:
        if agent.dispatch_response(msg):
            return
        if sampling.dispatch_response(msg):
            return
        print(f"⚠️  unmatched response id={msg.get('id')!r}", file=sys.stderr)
        return

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        resp = _handle_initialize(req_id, params)
    elif method == "describe":
        resp = _handle_describe(req_id)
    elif method == "invoke":
        resp = _handle_invoke(req_id, params)
    elif method == "health":
        resp = _handle_health(req_id)
    elif method == "shutdown":
        resp = _make_response(req_id, result={"ok": True})
    else:
        resp = _make_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    if req_id is not None:
        _write_frame(resp)


def main() -> None:
    print("🔌 executa-agent-demo plugin started (parity with anna-app iframe agent)", file=sys.stderr)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="invoke")
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            pool.submit(_handle_message, line)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
