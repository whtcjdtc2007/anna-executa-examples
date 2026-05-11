#!/usr/bin/env python3
"""storage_notebook.py — Executa plugin that demonstrates Anna Persistent Storage.

This example demonstrates:

* declaring ``client_capabilities.storage`` so the host knows we will use
  reverse-RPC ``storage/*`` and ``files/*``
* using :class:`executa_sdk.StorageClient` for cross-invoke persistent KV
* using :class:`executa_sdk.FilesClient` for two-step object uploads
* multiplexing the stdin reader across SamplingClient + StorageClient + FilesClient
  with :func:`executa_sdk.make_response_router`

Tools exposed:
- ``note_append``: append a timestamped entry to a per-user/app KV under
  ``"notes/log"`` (uses ``StorageClient.get`` + ``StorageClient.set`` with
  optimistic ``if_match`` retries).
- ``notes_list``: read back the current log.
- ``upload_attachment``: PUTs a small text payload to APS object storage via
  the host's presigned upload (two-step ``upload_begin`` → HTTP PUT →
  ``upload_complete``).

To enable storage end-to-end the published manifest must declare
``host_capabilities: ["aps.kv", "aps.files"]`` and the user must have an
APS quota assigned (default 5GB). Anna's host owns rate-limiting,
encryption, and per-app ACL — the plugin only sees its own scope.

Run locally with the anna-app-cli mock harness:

    pnpm anna-app dev --plugin storage_notebook.py
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
from typing import Any

import urllib.request

try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[3] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    PROTOCOL_VERSION_V2,
    FilesClient,
    StorageClient,
    StorageError,
    make_response_router,
)
from executa_sdk.storage import (  # noqa: E402
    STORAGE_ERR_PRECONDITION_FAILED,
)

# ─── Manifest ─────────────────────────────────────────────────────────

MANIFEST = {
    "name": "storage-notebook",
    "version": "0.1.1",
    "description": "Demo plugin showing Anna Persistent Storage reverse-RPC.",
    # Declare which host capabilities this plugin needs. The host (Matrix
    # Nexus) only mints a `storage_token` for invokes when the manifest
    # declares `aps.kv` / `aps.files` AND the user has enabled
    # `storage_grant` in their UserExecuta.custom_config. Without this
    # field, the plugin's storage/* reverse-RPC calls will fail with
    # STORAGE_NOT_GRANTED.
    "host_capabilities": ["aps.kv", "aps.files"],
    "tools": [
        {
            "name": "note_append",
            "description": "Append a note to the persistent log for the current app/user.",
            # NOTE: Executa protocol uses `parameters: [{name, type, required,
            # description, ...}]` (see docs/protocol-spec.md), NOT MCP-style
            # `input_schema: {type, properties, required}`. The host's
            # ToolDefinition.from_dict only reads `parameters` — using
            # `input_schema` makes the LLM see a tool with NO documented
            # arguments, which causes it to hallucinate keys like `content`.
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "Note text to append.",
                    "required": True,
                }
            ],
        },
        {
            "name": "notes_list",
            "description": "Return the persistent log of notes.",
            "parameters": [],
        },
        {
            "name": "upload_attachment",
            "description": "Upload a small text payload to APS object storage.",
            "parameters": [
                {
                    "name": "path",
                    "type": "string",
                    "description": "Object path under the user's APS bucket.",
                    "required": True,
                },
                {
                    "name": "text",
                    "type": "string",
                    "description": "UTF-8 text payload to upload.",
                    "required": True,
                },
            ],
        },
    ],
}

# ─── Reverse-RPC clients ──────────────────────────────────────────────

_storage = StorageClient()
_files = FilesClient()
_route_response = make_response_router(_storage, _files)

# Bound at runtime once we know which event loop reads stdin.
_loop: asyncio.AbstractEventLoop | None = None
_executor = ThreadPoolExecutor(max_workers=4)


# ─── Tool implementations ────────────────────────────────────────────


# This plugin is a standalone tool (not embedded in an Anna App), so we
# pin storage scope to ``"user"`` instead of relying on the SDK default
# ``"app"`` — the host won't mint a token covering ``app`` scope without
# an associated app_id, which a standalone tool doesn't have. Using
# ``"user"`` keeps notes per-user across the whole account; switch to
# ``"tool"`` if you want each plugin install to have a private notebook.
_SCOPE = "user"


async def _note_append(text: str) -> dict:
    """Append `text` to the notes log with optimistic concurrency."""
    key = "notes/log"
    for attempt in range(3):
        cur = await _storage.get(key, scope=_SCOPE)
        log = cur.get("value") if cur.get("exists") else []
        if not isinstance(log, list):
            log = []
        log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "text": text,
            }
        )
        try:
            res = await _storage.set(
                key,
                log,
                scope=_SCOPE,
                if_match=cur.get("etag") if cur.get("exists") else None,
            )
            return {"ok": True, "etag": res["etag"], "count": len(log)}
        except StorageError as e:
            if e.code == STORAGE_ERR_PRECONDITION_FAILED and attempt < 2:
                # somebody else wrote — retry
                continue
            raise
    return {"ok": False, "reason": "too many concurrent writers"}


async def _notes_list() -> dict:
    cur = await _storage.get("notes/log", scope=_SCOPE)
    return {"items": cur.get("value") or []}


async def _upload_attachment(path: str, text: str) -> dict:
    payload = text.encode("utf-8")
    info = await _files.upload_begin(
        path=path,
        size_bytes=len(payload),
        content_type="text/plain; charset=utf-8",
        scope=_SCOPE,
    )
    # PUT the bytes to the host-issued presigned URL.
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
    return {"ok": True, "path": path, "etag": etag, "complete": res}


# ─── stdio loop ───────────────────────────────────────────────────────


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(req_id: Any, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id: Any, code: int, message: str, data: dict | None = None) -> None:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    _write({"jsonrpc": "2.0", "id": req_id, "error": err})


async def _handle_invoke(req_id: Any, params: dict) -> None:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    try:
        if tool == "note_append":
            data = await _note_append(str(args.get("text", "")))
        elif tool == "notes_list":
            data = await _notes_list()
        elif tool == "upload_attachment":
            data = await _upload_attachment(
                str(args.get("path")), str(args.get("text", ""))
            )
        else:
            _err(req_id, -32601, f"Unknown tool: {tool}")
            return
        # NOTE: Matrix host's InvokeResult.from_dict reads
        # `result["success"]` (default False) and `result["data"]`. The
        # tool's payload MUST be wrapped — returning the bare tool dict
        # makes the host treat every successful invoke as a failure
        # (with no error message), surfacing as
        # `{"success": false, "command_id": "..."}` to the LLM.
        _ok(req_id, {"success": True, "tool": tool, "data": data})
    except StorageError as e:
        _err(req_id, e.code, e.message, e.data)
    except Exception as e:  # pragma: no cover - defensive
        _err(req_id, -32603, f"plugin error: {e}")


def _handle_initialize(req_id: Any, params: dict) -> None:
    _ok(
        req_id,
        {
            "protocolVersion": PROTOCOL_VERSION_V2,
            "serverInfo": {"name": MANIFEST["name"], "version": MANIFEST["version"]},
            # Declare we want to use APS reverse RPC.
            "capabilities": {"storage": {"kv": True, "files": True}},
        },
    )


def _stdin_loop() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Responses to OUR reverse-RPC requests get routed first.
        if "method" not in msg and _route_response(msg):
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            _handle_initialize(req_id, msg.get("params") or {})
        elif method == "describe":
            # NOTE: `result` MUST be the manifest itself — Matrix's
            # ToolManifest.from_dict reads data["name"] directly. Wrapping
            # the manifest in {"manifest": MANIFEST} causes a host-side
            # KeyError: 'name' and the plugin is dropped at load time.
            _ok(req_id, MANIFEST)
        elif method == "health":
            _ok(req_id, {"status": "ok"})
        elif method == "shutdown":
            _ok(req_id, {})
            return
        elif method == "invoke":
            assert _loop is not None
            asyncio.run_coroutine_threadsafe(
                _handle_invoke(req_id, msg.get("params") or {}), _loop
            )
        else:
            _err(req_id, -32601, f"method not found: {method}")


async def _main() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    t = threading.Thread(target=_stdin_loop, daemon=True)
    t.start()
    # Block until stdin closes (parent killed us).
    while t.is_alive():
        await asyncio.sleep(1)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
