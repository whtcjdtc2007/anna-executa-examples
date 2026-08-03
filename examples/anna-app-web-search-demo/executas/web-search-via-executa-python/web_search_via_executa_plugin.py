#!/usr/bin/env python3
"""web_search_via_executa_plugin.py — Executa that demonstrates the
**web/search** and **web/fetch** reverse-RPCs end to end.

See matrix-nexus ``docs/design/app-web-search.md`` §4 (Reverse RPC channel)
and https://anna.partners/developers/reference (Host API · web.*).

Why route web access through the host
-------------------------------------
The plugin never holds a search-provider API key: provider routing
(tavily → ddgs degrade chain), SSRF guarding, quota, billing and audit all
live host-side. The plugin just asks — ``web/search`` / ``web/fetch`` —
and gets back a provider-agnostic envelope. ``search_depth`` expresses a
*quality intent*, never a provider choice; the response reports the
ACTUALLY-executed ``provider_tier`` and bills the cheaper rate on degrade.

stdio-channel budgets are tighter than the iframe HOST API twin:
fetch is capped at 8,000 chars/page and 256 KB per response.

This Executa exposes two tools:

- ``web_search``   — one reverse ``web/search`` round-trip. Returns the
                     bare results so the UI can compare them 1:1 with the
                     iframe HOST API channel (same wire contract).
- ``web_research`` — a minimal research pipeline: search a topic, then
                     ``web/fetch`` the top N result pages and return their
                     extracted Markdown. Demonstrates per-item failure
                     isolation (a blocked page never fails the batch).

Run it::

    anna-app dev               # signed-in account; web_grant seeded in dev

End-to-end enablement outside dev: the manifest must declare
``host_capabilities: ["web.search", "web.fetch"]`` (below) — re-register
after changing it so the caps reach ``manifest_cache`` — and the user must
enable the Web toggle for this Executa in Anna's Permissions panel
(writes ``web_grant.enabled = true`` on ``UserExecuta.custom_config``).
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Fallback for fresh checkouts: locate the in-repo SDK when not pip-installed.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[4] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    WebClient,
    WebError,
    make_response_router,
)

PROTOCOL_VERSION_V2 = "2.0"

# ─── Manifest ─────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Web Search via Executa",
    "version": "0.2.0",
    "description": (
        "Demonstrates the web/search + web/fetch + web/image_search + "
        "web/image_fetch reverse-RPCs: the host owns provider keys, SSRF "
        "guarding, quota and billing; the plugin just asks."
    ),
    "author": "Anna Developer",
    # `web.search` / `web.fetch` — required for the reverse web/* flow.
    # Phase 2 adds `web.image_search` / `web.image_fetch` (image search +
    # image download → APS artifact). Without them the host refuses with
    # WEB_NOT_GRANTED (-32521); the user must also have `web_grant.enabled`
    # (+ allowImageSearch / allowImageFetch, which default OFF) on their
    # UserExecuta.custom_config (the `anna-app dev` install seeds a
    # fully-enabled dev grant).
    "host_capabilities": [
        "web.search",
        "web.fetch",
        "web.image_search",
        "web.image_fetch",
    ],
    "tools": [
        {
            "name": "web_search",
            "description": (
                "Search the web (or news) through the host's managed providers "
                "and return provider-agnostic results. One reverse web/search "
                "round-trip — billed to the user (ddgs basic = 0.5 CU floor)."
            ),
            # Executa protocol uses `parameters: [{name, type, required, ...}]`
            # (see docs/protocol-spec.md), NOT MCP-style `input_schema`. The
            # host's ToolDefinition.from_dict only reads `parameters`.
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "description": "Search query (≤ 400 chars).",
                    "required": True,
                },
                {
                    "name": "max_results",
                    "type": "integer",
                    "description": "Max results, 1-10 (default 5).",
                    "required": False,
                    "default": 5,
                },
                {
                    "name": "topic",
                    "type": "string",
                    "description": "general or news.",
                    "required": False,
                    "default": "general",
                    "enum": ["general", "news"],
                },
                {
                    "name": "time_range",
                    "type": "string",
                    "description": "Optional recency filter.",
                    "required": False,
                    "enum": ["day", "week", "month", "year"],
                },
            ],
        },
        {
            "name": "web_research",
            "description": (
                "Minimal research pipeline: web/search a topic, then web/fetch "
                "the top N result pages (SSRF-guarded extraction to Markdown). "
                "Per-item failure isolation — check pages[i].ok."
            ),
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "description": "Research topic.",
                    "required": True,
                },
                {
                    "name": "pages",
                    "type": "integer",
                    "description": "How many top results to fetch, 1-3 (default 2).",
                    "required": False,
                    "default": 2,
                },
            ],
        },
        {
            "name": "web_image_search",
            "description": (
                "Search the web for images through the host's managed providers "
                "(Phase 2). Safe-search is force-enabled host-side; results are "
                "provider-agnostic {image_url, thumbnail_url, source_url, …}. "
                "Requires web_grant.allowImageSearch (defaults OFF)."
            ),
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "description": "Image search query (≤ 400 chars).",
                    "required": True,
                },
                {
                    "name": "max_results",
                    "type": "integer",
                    "description": "Max results, 1-50 (default 6).",
                    "required": False,
                    "default": 6,
                },
                {
                    "name": "aspect",
                    "type": "string",
                    "description": "Optional aspect filter.",
                    "required": False,
                    "default": "any",
                    "enum": ["any", "wide", "tall", "square"],
                },
            ],
        },
        {
            "name": "web_image_grab",
            "description": (
                "Download one image via the host (SSRF-guarded, MIME whitelist "
                "+ magic-byte sniffing) into APS file storage and return the "
                "artifact reference {path, get_url, sha256, …} — never bytes "
                "(stdio-frame safe). Requires web_grant.allowImageFetch."
            ),
            "parameters": [
                {
                    "name": "url",
                    "type": "string",
                    "description": "http(s) image URL to download.",
                    "required": True,
                },
                {
                    "name": "purpose",
                    "type": "string",
                    "description": "Optional audit label.",
                    "required": False,
                },
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}

# ─── Reverse-RPC client ───────────────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


_web = WebClient(write_frame=_write_frame)
_route_response = make_response_router(_web)


# ─── Tool implementations ─────────────────────────────────────────────


async def _web_search(
    query: str, max_results: int, topic: str, time_range: str
) -> dict:
    if not query.strip():
        raise ValueError("query must be non-empty")
    result = await _web.search(
        query=query,
        max_results=max(1, min(10, int(max_results or 5))),
        topic=topic if topic in ("general", "news") else "general",
        time_range=time_range if time_range in ("day", "week", "month", "year") else None,
    )
    return {
        "ok": True,
        "channel": "reverse-rpc",
        "results": result.get("results", []),
        # The ACTUALLY-executed tier — an advanced request degraded to ddgs
        # comes back "basic" and is billed the cheaper rate.
        "provider_tier": result.get("provider_tier"),
        "quota_consumed": result.get("quota_consumed"),
    }


async def _web_research(query: str, pages: int) -> dict:
    if not query.strip():
        raise ValueError("query must be non-empty")
    pages = max(1, min(3, int(pages or 2)))

    found = await _web.search(query=query, max_results=pages)
    results = found.get("results", [])
    if not results:
        return {
            "ok": True,
            "channel": "reverse-rpc",
            "query": query,
            "results": [],
            "pages": [],
            "note": "no results",
        }

    urls = [r["url"] for r in results[:pages]]
    # stdio budget: ≤ 8,000 chars/page — ask for less to keep the invoke
    # reply comfortably under the frame limit.
    fetched = await _web.fetch(urls=urls, format="markdown", max_chars=4000)

    # Per-item failure isolation: a blocked/broken page (SSRF_BLOCKED,
    # HTTP_404, TIMEOUT, …) never fails the batch; report it inline.
    return {
        "ok": True,
        "channel": "reverse-rpc",
        "query": query,
        "results": results,
        "pages": fetched.get("pages", []),
        "quota_consumed": round(
            float(found.get("quota_consumed") or 0)
            + float(fetched.get("quota_consumed") or 0),
            4,
        ),
    }


async def _web_image_search(query: str, max_results: int, aspect: str) -> dict:
    if not query.strip():
        raise ValueError("query must be non-empty")
    result = await _web.image_search(
        query=query,
        max_results=max(1, min(50, int(max_results or 6))),
        aspect=aspect if aspect in ("any", "wide", "tall", "square") else None,
    )
    out = {
        "ok": True,
        "channel": "reverse-rpc",
        "results": result.get("results", []),
        "quota_consumed": result.get("quota_consumed"),
        "cached": result.get("cached", False),
    }
    # Diagnostic passthrough (forum #193): `_meta.provider` says which
    # provider actually served this call — serper (Google Images) or the
    # ddgs/tavily fallbacks. Optional; never depend on its value.
    if result.get("_meta") is not None:
        out["_meta"] = result["_meta"]
    return out


async def _web_image_grab(url: str, purpose: str) -> dict:
    if not url.strip():
        raise ValueError("url must be non-empty")
    # The host downloads (SSRF + MIME whitelist + magic bytes) and stores
    # into APS files (tool-scope); we get back an artifact REFERENCE —
    # path + short-lived get_url — never the bytes (stdio-frame safe).
    result = await _web.image_fetch(url=url, purpose=purpose or None)
    return {"ok": True, "channel": "reverse-rpc", **result}


# ─── JSON-RPC dispatch ────────────────────────────────────────────────


def _ok(req_id: Any, result: dict) -> None:
    _write_frame({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id: Any, code: int, message: str, data: dict | None = None) -> None:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    _write_frame({"jsonrpc": "2.0", "id": req_id, "error": err})


_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()


def _handle_invoke(req_id: Any, params: dict) -> None:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    if tool == "web_search":
        coro = _web_search(
            str(args.get("query", "")),
            args.get("max_results", 5),
            str(args.get("topic", "general")),
            str(args.get("time_range", "")),
        )
    elif tool == "web_research":
        coro = _web_research(str(args.get("query", "")), args.get("pages", 2))
    elif tool == "web_image_search":
        coro = _web_image_search(
            str(args.get("query", "")),
            args.get("max_results", 6),
            str(args.get("aspect", "any")),
        )
    elif tool == "web_image_grab":
        coro = _web_image_grab(
            str(args.get("url", "")),
            str(args.get("purpose", "")),
        )
    else:
        _err(req_id, -32601, f"Unknown tool: {tool}")
        return

    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        data = fut.result(timeout=180.0)
    except WebError as e:
        # web/* surfaces WEB_* codes (-32521 not granted, -32522 CU quota
        # exhausted, -32523 provider chain down, …).
        # Pass them through verbatim so the LLM/UI sees the real reason.
        _err(req_id, e.code, e.message, e.data)
        return
    except ValueError as e:
        _err(req_id, -32602, str(e))
        return
    except Exception as e:  # noqa: BLE001
        _err(req_id, -32603, f"Tool execution failed: {e}")
        return
    # InvokeResult.from_dict on the host expects {success, data}; never
    # return the bare tool dict (it would be read as success=False).
    _ok(req_id, {"success": True, "tool": tool, "data": data})


def _handle_initialize(req_id: Any, params: dict) -> None:
    proto = (params or {}).get("protocolVersion") or PROTOCOL_VERSION_V2
    is_v2 = proto == PROTOCOL_VERSION_V2
    if not is_v2:
        # web/* is v2-only; disable so calls fail fast with a clear reason
        # instead of hanging on a never-answered reverse-RPC.
        _web.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "web/search + web/fetch require Executa protocol 2.0"
        )
    _ok(
        req_id,
        {
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {
                "name": MANIFEST["display_name"],
                "version": MANIFEST["version"],
            },
            "client_capabilities": {"web": {}} if is_v2 else {},
        },
    )


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    # Reverse-RPC replies from the host resolve our pending web futures.
    if "method" not in msg:
        if not _route_response(msg):
            print(f"⚠️  unmatched response id={msg.get('id')!r}", file=sys.stderr)
        return

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        _handle_initialize(req_id, params)
    elif method == "describe":
        # `result` MUST be the bare manifest — matrix's ToolManifest.from_dict
        # reads result["name"] directly.
        _ok(req_id, MANIFEST)
    elif method == "health":
        _ok(req_id, {"status": "healthy", "version": MANIFEST["version"]})
    elif method == "invoke":
        _handle_invoke(req_id, params)
    elif method == "shutdown":
        _ok(req_id, {"ok": True})
    elif req_id is not None:
        _err(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    print("🔌 web-search-via-executa plugin started", file=sys.stderr)
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
