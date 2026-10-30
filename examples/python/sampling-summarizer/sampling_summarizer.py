#!/usr/bin/env python3
"""sampling_summarizer.py — Executa plugin that uses host LLM sampling.

This example demonstrates:

* the v2 ``initialize`` handshake (advertising ``client_capabilities.sampling``)
* issuing a reverse JSON-RPC ``sampling/createMessage`` request to the host
* sharing one stdin reader between agent-initiated invokes and host responses

The plugin exposes a single tool ``summarize`` that accepts a long string and
asks the host to return a one-paragraph summary. Anna's host owns model
selection, billing, and quota — the plugin never holds an LLM API key.

To enable sampling end-to-end:

1. Declare ``host_capabilities: ["llm.sample"]`` in the published manifest
   (see ``MANIFEST`` below).
2. The end user must have toggled ``sampling_grant.enabled = true`` for this
   Executa in their Anna Admin panel.

Run locally with the anna-app-cli mock harness:

    pnpm anna-app dev --plugin sampling_summarizer.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Allow running this file directly from a fresh checkout without first
# installing the SDK: if `executa_sdk` is not importable, fall back to
# the in-repo copy at anna-executa-examples/sdk/python/.
# When installed via `uv tool install .` / pip / pipx, the SDK is a
# normal dependency and this fallback is a no-op.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[3] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

import asyncio  # noqa: E402

from executa_sdk import (  # noqa: E402
    METHOD_SAMPLING_CREATE_MESSAGE,
    PROTOCOL_VERSION_V2,
    SamplingClient,
    SamplingError,
)

# ─── Manifest ────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Sampling Summarizer",
    "version": "0.1.0",
    "description": "Summarizes a passage of text by asking the host to sample an LLM.",
    "author": "Anna Developer",
    # NEW in v2 — declares which reverse capabilities this plugin will use.
    # Without this entry, Nexus will refuse the plugin's sampling requests
    # with error -32008 (NOT_NEGOTIATED).
    "host_capabilities": ["llm.sample"],
    "tools": [
        {
            "name": "summarize",
            "description": "Summarize the supplied text into one short paragraph.",
            "parameters": [
                {"name": "text", "type": "string", "description": "Text to summarize", "required": True},
                {"name": "max_words", "type": "integer", "description": "Approx max words in summary", "required": False, "default": 80},
            ],
        },
        {
            "name": "summarize_structured",
            "description": (
                "Summarize the supplied text into structured JSON "
                "{summary, keywords, sentiment} using host structured output "
                "(sampling responseFormat)."
            ),
            "parameters": [
                {"name": "text", "type": "string", "description": "Text to analyse", "required": True},
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ─── Sampling client + bookkeeping ────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


sampling = SamplingClient(write_frame=_write_frame)


# ─── Tool implementation ─────────────────────────────────────────────


async def _summarize(text: str, max_words: int = 80, *, invoke_id: str) -> dict:
    """Ask the host LLM to summarize ``text``."""
    if not text or not text.strip():
        return {"summary": "", "note": "empty input"}

    max_words = max(20, min(400, int(max_words)))
    # ~4 chars/token rough estimate, +slack
    max_tokens = max(64, min(1024, max_words * 5))

    result = await sampling.create_message(
        messages=[
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Summarize the following text in at most {max_words} words. "
                        "Return only the summary, no preamble.\n\n---\n" + text
                    ),
                },
            }
        ],
        max_tokens=max_tokens,
        system_prompt="You are a concise editorial assistant.",
        # No `model_preferences` → host falls back to the user's
        # `preferred_model` saved in their Anna account.
        # Thinking is OFF by default for sampling; pass `thinking=True` or
        # `thinking={"effort": "low"}` to opt in (costs reasoning tokens).
        metadata={"executa_invoke_id": invoke_id, "tool": "summarize"},
        timeout=60.0,
    )

    text_out = ""
    content = result.get("content") or {}
    if isinstance(content, dict) and content.get("type") == "text":
        text_out = content.get("text", "")
    return {
        "summary": text_out,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "stopReason": result.get("stopReason"),
    }


# JSON Schema for the structured summary — host-enforced hard limits:
# ≤32KB serialized, nesting depth ≤8, ≤512 nodes.
_STRUCTURED_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One-paragraph summary"},
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-8 salient keywords",
        },
        "sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative"],
        },
    },
    "required": ["summary", "keywords", "sentiment"],
    "additionalProperties": False,
}


async def _summarize_structured(text: str, *, invoke_id: str) -> dict:
    """Structured-output variant: constrain the model at the protocol level.

    Demonstrates `responseFormat` (L2 `json_schema`) with an explicit
    `on_unsupported="json_object"` downgrade: if the user's selected model
    lacks strict-schema support, the host falls back to plain JSON mode
    instead of failing the invoke with -32010. Check
    `_meta.responseFormat.downgraded` to learn which path was taken.
    """
    if not text or not text.strip():
        return {"summary": "", "keywords": [], "sentiment": "neutral", "note": "empty input"}

    result = await sampling.create_message(
        messages=[
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        "Analyse the following text. Reply with a JSON object "
                        "containing: summary (one paragraph), keywords (3-8 strings), "
                        "sentiment (positive|neutral|negative).\n\n---\n" + text
                    ),
                },
            }
        ],
        max_tokens=512,
        system_prompt="You are a precise editorial analyst. Reply with JSON only.",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "summary_analysis",
                "strict": True,
                "schema": _STRUCTURED_SUMMARY_SCHEMA,
            },
        },
        on_unsupported="json_object",  # downgrade to L1 instead of -32010
        metadata={"executa_invoke_id": invoke_id, "tool": "summarize_structured"},
        timeout=60.0,
    )

    raw = ""
    content = result.get("content") or {}
    if isinstance(content, dict) and content.get("type") == "text":
        raw = content.get("text", "")
    rf_meta = (result.get("_meta") or {}).get("responseFormat") or {}

    # The host guarantees `content.text` stays a string and only reports
    # `structuredValid` informationally — the final parse is ours.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "error": "model returned non-JSON output",
            "raw": raw[:500],
            "responseFormat": rf_meta,
        }
    return {
        **data,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "responseFormat": rf_meta,
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
    """Respond to the host's v2 handshake. We accept v2 if offered, else v1."""
    proto = (params or {}).get("protocolVersion") or "1.1"
    if proto != PROTOCOL_VERSION_V2:
        # v1 host — sampling is unavailable; surface a clear error if used.
        sampling.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "sampling/createMessage requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {
                "name": MANIFEST["display_name"],
                "version": MANIFEST["version"],
            },
            # Mirror MCP shape: advertise that we WILL use sampling.
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

    if tool == "summarize":
        coro = _summarize(invoke_id=invoke_id, **args)
    elif tool == "summarize_structured":
        coro = _summarize_structured(invoke_id=invoke_id, **args)
    else:
        return _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown tool: {tool}"},
        )

    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
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


# ─── Main loop ───────────────────────────────────────────────────────


def main() -> None:
    print("🔌 sampling-summarizer plugin started", file=sys.stderr)
    # Invokes may run concurrently — each blocks on a reverse RPC; offload
    # them to a small worker pool so the stdin reader stays responsive to
    # both new invokes and incoming sampling responses.
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
