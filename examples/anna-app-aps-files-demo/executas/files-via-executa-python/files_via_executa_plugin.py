#!/usr/bin/env python3
"""files_via_executa_plugin.py — Executa that stores attachments in
Anna Persistent Storage (APS Files / object storage) on behalf of the
calling Anna App.

The whole point of this demo is to show the *best-practice* wiring for
APS Files through an Executa:

    iframe ── anna.tools.invoke ──▶ this Executa ── files/* reverse-RPC ──▶ host

The hosting Anna App's manifest only needs::

    "ui": { "host_api": { "tools": ["required:bundled:files-via-executa"] } }

It does **NOT** need ``host.files`` / ``host.upload`` / any
``ui.host_api.files`` or ``ui.host_api.upload`` grant. Object storage is
reached through *this Executa's own* ``aps.files`` capability + the
user's storage grant — the host mints a ``storage_token`` scoped to the
Executa and routes ``files/*`` over the Executa's reverse-RPC channel,
never through the app iframe's ACL.

Tools exposed:
- ``save_note``    — upload a UTF-8 text payload to a path under the
                     user's APS bucket (two-step ``upload_begin`` →
                     HTTP PUT → ``upload_complete``).
- ``get_link``     — mint a short-lived presigned ``download_url`` for a
                     previously-saved path.
- ``list_notes``   — list objects under an optional prefix.

To run this end-to-end you need real APS:

    anna-app dev --storage aps        # mints a real storage_token

With the plain in-memory harness (``anna-app dev`` without
``--storage aps``) the ``files/*`` reverse-RPC returns a clean
``not_implemented`` (-32004) because there is no object-storage backend
locally — that is expected, not a bug.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import urllib.request
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
    PROTOCOL_VERSION_V2,
    FilesClient,
    StorageError,
    make_response_router,
)

# ─── Manifest ─────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Files via Executa",
    "version": "0.1.0",
    "description": (
        "Stores text attachments in Anna Persistent Storage (APS Files) "
        "on behalf of the calling app via the files/* reverse-RPC."
    ),
    "author": "Anna Developer",
    # Required for APS object storage. Without `aps.files` the host refuses
    # the files/* reverse-RPC with STORAGE_NOT_GRANTED. The user must also
    # have enabled `storage_grant` on their UserExecuta.custom_config.
    "host_capabilities": ["aps.files"],
    "tools": [
        {
            "name": "save_note",
            "description": (
                "Upload a UTF-8 text note to object storage at the given "
                "path and return its size + etag."
            ),
            # Executa protocol uses `parameters: [{name, type, required, ...}]`
            # (see docs/protocol-spec.md), NOT MCP-style `input_schema`. The
            # host's ToolDefinition.from_dict only reads `parameters`.
            "parameters": [
                {
                    "name": "path",
                    "type": "string",
                    "description": "Object path under the user's APS bucket, e.g. 'notes/today.txt'.",
                    "required": True,
                },
                {
                    "name": "text",
                    "type": "string",
                    "description": "UTF-8 text payload to store.",
                    "required": True,
                },
            ],
        },
        {
            "name": "get_link",
            "description": "Mint a short-lived presigned download URL for a saved note.",
            "parameters": [
                {
                    "name": "path",
                    "type": "string",
                    "description": "Object path previously saved via save_note.",
                    "required": True,
                }
            ],
        },
        {
            "name": "list_notes",
            "description": "List saved notes under an optional path prefix.",
            "parameters": [
                {
                    "name": "prefix",
                    "type": "string",
                    "description": "Optional path prefix to filter by, e.g. 'notes/'.",
                    "required": False,
                    "default": "",
                }
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


_files = FilesClient(write_frame=_write_frame)
_route_response = make_response_router(_files)

# This Executa is bundled inside an Anna App but APS Files objects live in
# the *user's* namespace (so the user can find them again from the Anna
# chat UI). Pin scope to "user" rather than the SDK default "app".
_SCOPE = "user"


# ─── Tool implementations ─────────────────────────────────────────────


async def _save_note(path: str, text: str) -> dict:
    payload = text.encode("utf-8")
    info = await _files.upload_begin(
        path=path,
        size_bytes=len(payload),
        content_type="text/plain; charset=utf-8",
        scope=_SCOPE,
    )
    # PUT the bytes straight to the host-issued presigned URL. The Executa
    # never proxies the body through the host — it goes object-store direct.
    req = urllib.request.Request(
        info["put_url"],
        data=payload,
        method="PUT",
        headers=info.get("headers") or {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - presigned URL
        etag = resp.headers.get("ETag") or resp.headers.get("etag")
        status = resp.status
    if status not in (200, 201):
        raise RuntimeError(f"object upload PUT failed: HTTP {status}")
    res = await _files.upload_complete(
        path=path, etag=etag, size_bytes=len(payload), scope=_SCOPE
    )
    return {
        "ok": True,
        "path": path,
        "size_bytes": len(payload),
        "etag": etag,
        "complete": res,
    }


async def _get_link(path: str) -> dict:
    res = await _files.download_url(path=path, expires_in=600, scope=_SCOPE)
    return {"path": path, "url": res.get("url"), "expires_at": res.get("expires_at")}


async def _list_notes(prefix: str = "") -> dict:
    res = await _files.list(prefix=prefix or None, scope=_SCOPE)
    return {"items": res.get("items") or [], "next_cursor": res.get("next_cursor")}


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
    if tool == "save_note":
        coro = _save_note(str(args.get("path")), str(args.get("text", "")))
    elif tool == "get_link":
        coro = _get_link(str(args.get("path")))
    elif tool == "list_notes":
        coro = _list_notes(str(args.get("prefix", "")))
    else:
        _err(req_id, -32601, f"Unknown tool: {tool}")
        return

    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        data = fut.result(timeout=90.0)
    except StorageError as e:
        # The local harness returns -32004 not_implemented when run without
        # `--storage aps`; surface it verbatim so the LLM/UI sees the reason.
        _err(req_id, e.code, e.message, e.data)
        return
    except Exception as e:  # noqa: BLE001
        _err(req_id, -32603, f"Tool execution failed: {e}")
        return
    # InvokeResult.from_dict on the host expects {success, data}; never
    # return the bare tool dict (it would be read as success=False).
    _ok(req_id, {"success": True, "tool": tool, "data": data})


def _handle_initialize(req_id: Any, params: dict) -> None:
    proto = (params or {}).get("protocolVersion") or PROTOCOL_VERSION_V2
    _ok(
        req_id,
        {
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            # Declare we want APS Files reverse-RPC.
            "capabilities": {"storage": {"files": True}},
        },
    )


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    # Reverse-RPC replies from the host resolve our pending files futures.
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
    print("🔌 files-via-executa plugin started", file=sys.stderr)
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
