#!/usr/bin/env python3
"""embed_via_executa_plugin.py — minimal Executa that computes text
embeddings on behalf of the calling app.

The point of this plugin is to *demonstrate* the alternative path the
Embed Demo app exposes in its UI:

    iframe ── anna.tools.invoke ──▶ this Executa ── embeddings/create ──▶ host

It is intentionally tiny — one tool, ``embed``, that wraps the host
``embeddings/create`` reverse-RPC. Use it side-by-side with the
``anna.llm.embed`` path the same app exercises to compare the two
embedding access surfaces:

* ``anna.llm.embed`` — direct call from iframe → host embeddings.
  Billed per-token to the end-user, governed by ``host_api.llm`` grants.
* ``anna.tools.invoke`` → this Executa → ``embeddings/create`` —
  indirect, billed per-token to the end-user via the Executa's
  ``embed_grant``, governed by ``host_capabilities: ["llm.embed"]``.

Mirrors examples/anna-app-llm-demo/executas/llm-via-executa-python/.
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
    EmbeddingsClient,
    EmbeddingsError,
)

# ─── Manifest ────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Embed via Executa",
    "version": "0.1.0",
    "description": (
        "Computes text embeddings on behalf of the calling app by "
        "asking the host (embeddings/create reverse-RPC)."
    ),
    "author": "Anna Developer",
    # Required for v2 reverse embeddings. Without this, the host will
    # refuse the reverse-RPC with -32501 (EMBED_NOT_GRANTED).
    "host_capabilities": ["llm.embed"],
    "tools": [
        {
            "name": "embed",
            "description": (
                "Compute embedding vectors for one or more texts. The "
                "host picks the backing model and bills per token."
            ),
            "parameters": [
                {
                    "name": "texts",
                    "type": "array",
                    "items_type": "string",
                    "description": "List of texts to embed (1..N).",
                    "required": True,
                },
                {
                    "name": "model",
                    "type": "string",
                    "description": (
                        "Host-stable model alias (e.g. 'anna-managed-v1'). "
                        "Default: 'anna-managed-v1'."
                    ),
                    "required": False,
                    "default": "anna-managed-v1",
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


embeddings = EmbeddingsClient(write_frame=_write_frame)


# ─── Tool implementation ─────────────────────────────────────────────


async def _embed(texts, model: str = "anna-managed-v1", *, invoke_id: str) -> dict:
    if not texts:
        raise EmbeddingsError(-32504, "texts must be non-empty")
    if isinstance(texts, str):
        texts = [texts]
    inputs = [str(t) for t in texts]

    result = await embeddings.create(
        input=inputs,
        model=model or "anna-managed-v1",
        timeout=30.0,
    )

    data = result.get("data") or []
    first_vec = data[0].get("embedding") if data else []
    meta = result.get("_meta") or {}

    return {
        "count": len(data),
        "dimensions": meta.get("dimensions") or (len(first_vec) if first_vec else 0),
        "first_vector_preview": first_vec[:8],
        "model": result.get("model") or model,
        "usage": result.get("usage"),
        "_meta": {
            "latencyMs": meta.get("latencyMs"),
            "costUsd": meta.get("costUsd"),
            "backendModel": meta.get("backendModel"),
            "provider": meta.get("provider"),
        },
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
        embeddings.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "embeddings/create requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": {"embeddings": {}} if proto == PROTOCOL_VERSION_V2 else {},
            "capabilities": {},
        },
    )


def _handle_describe(req_id) -> dict:
    # NOTE: matrix host's ToolManifest.from_dict reads result["name"]
    # directly, so the bare manifest MUST be the result (not wrapped).
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

    if tool != "embed":
        return _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown tool: {tool}"},
        )

    fut = asyncio.run_coroutine_threadsafe(
        _embed(invoke_id=invoke_id, **args), _loop
    )
    try:
        data = fut.result(timeout=60.0)
    except EmbeddingsError as e:
        return _make_response(
            req_id,
            error={"code": e.code, "message": e.message, "data": e.data},
        )
    except Exception as e:  # noqa: BLE001
        return _make_response(
            req_id,
            error={"code": -32603, "message": f"Tool execution failed: {e}"},
        )
    # InvokeResult.from_dict on the host expects {success, data}; do
    # NOT return the bare tool dict.
    return _make_response(req_id, result={"success": True, "tool": tool, "data": data})


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply from host → resolve a pending embeddings future.
    if "method" not in msg:
        if not embeddings.dispatch_response(msg):
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
    print("🔌 embed-via-executa plugin started", file=sys.stderr)
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
