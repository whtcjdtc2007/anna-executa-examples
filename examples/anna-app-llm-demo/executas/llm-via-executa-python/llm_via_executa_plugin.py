#!/usr/bin/env python3
"""llm_via_executa_plugin.py — minimal Executa that performs an LLM
completion on behalf of the calling app.

The point of this plugin is to *demonstrate* the alternative path the
LLM Demo app exposes in its UI:

    iframe ── anna.tools.invoke ──▶ this Executa ── sampling/createMessage ──▶ host LLM

It is intentionally tiny — one tool, ``complete``, that wraps the host
``sampling/createMessage`` reverse-RPC. Use it side-by-side with the
``anna.llm.complete`` path the same app exercises to compare the two
LLM access surfaces:

* ``anna.llm.complete`` — direct call from iframe → host LLM. Billed
  to the end-user, governed by ``host_api.llm`` grants.
* ``anna.tools.invoke`` → this Executa → ``sampling/createMessage`` —
  indirect, billed to the end-user via the Executa's
  ``sampling_grant``, governed by ``host_capabilities: ["llm.sample"]``.

Mostly mirrors examples/python/sampling-summarizer; the differences:
* one tool (``complete``) instead of one summarizer
* the system prompt + user prompt come from the caller verbatim
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
    SamplingClient,
    SamplingError,
)

# ─── Manifest ────────────────────────────────────────────────────────
# The host keys this plugin by the *server-assigned tool_id* (the
# registration key it resolves from the on-disk shim name / nexus
# ``executable_name or tool_id``), NOT by any name declared here. A
# self-declared ``name`` is therefore optional and purely diagnostic;
# we omit it so there is no placeholder to keep in sync with the minted
# tool_id. The app's manifest.json (required_executas + host_api.tools)
# and the on-disk shim name are what bind this Executa to its tool_id.

MANIFEST = {
    "display_name": "LLM via Executa",
    "version": "0.1.0",
    "description": (
        "Performs an LLM completion on behalf of the calling app by "
        "asking the host to sample (sampling/createMessage)."
    ),
    "author": "Anna Developer",
    # Required for v2 reverse sampling. Without this, the host will
    # refuse sampling requests with error -32008 (NOT_NEGOTIATED).
    "host_capabilities": ["llm.sample"],
    "tools": [
        {
            "name": "complete",
            "description": (
                "Run a single-turn LLM completion. The host picks the "
                "model and bills the user's quota."
            ),
            "parameters": [
                {
                    "name": "prompt",
                    "type": "string",
                    "description": "User prompt to send to the LLM.",
                    "required": True,
                },
                {
                    "name": "system_prompt",
                    "type": "string",
                    "description": "Optional system instruction.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "max_tokens",
                    "type": "integer",
                    "description": "Max output tokens (16-4096).",
                    "required": False,
                    "default": 256,
                },
            ],
        }
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


sampling = SamplingClient(write_frame=_write_frame)


# ─── Tool implementation ─────────────────────────────────────────────


async def _complete(prompt: str, system_prompt: str = "", max_tokens: int = 256, *, invoke_id: str) -> dict:
    if not prompt or not prompt.strip():
        return {"text": "", "note": "empty prompt"}

    max_tokens = max(16, min(4096, int(max_tokens)))

    result = await sampling.create_message(
        messages=[
            {
                "role": "user",
                "content": {"type": "text", "text": prompt},
            }
        ],
        max_tokens=max_tokens,
        system_prompt=system_prompt or None,
        metadata={"executa_invoke_id": invoke_id, "tool": "complete"},
        timeout=60.0,
    )

    text_out = ""
    content = result.get("content") or {}
    if isinstance(content, dict) and content.get("type") == "text":
        text_out = content.get("text", "")
    return {
        "text": text_out,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "stopReason": result.get("stopReason"),
    }


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
        sampling.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "sampling/createMessage requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": {"sampling": {}} if proto == PROTOCOL_VERSION_V2 else {},
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
    invoke_id = params.get("invoke_id") or ""

    if tool != "complete":
        return _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown tool: {tool}"},
        )

    fut = asyncio.run_coroutine_threadsafe(
        _complete(invoke_id=invoke_id, **args), _loop
    )
    try:
        data = fut.result(timeout=120.0)
    except SamplingError as e:
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

    # Reverse-RPC reply from host → resolve a pending sampling future.
    if "method" not in msg:
        if not sampling.dispatch_response(msg):
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
    print("🔌 llm-via-executa plugin started", file=sys.stderr)
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
