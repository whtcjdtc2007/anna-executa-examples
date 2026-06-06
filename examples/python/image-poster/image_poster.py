#!/usr/bin/env python3
"""image_poster.py — Executa v2 plugin that generates posters via host LLM image.

Demonstrates THREE Executa v2 reverse-RPCs in one tool:

1. ``image/generate`` — ask the host to produce an image from a text prompt.
2. ``image/edit``     — (optional) restyle the generated image via a second pass.
3. ``host/uploadFile``— (optional) persist the final bytes back to host storage,
                         e.g. so the user can attach the poster to a chat message.

The host (Nexus) owns provider selection, grant checks, quota, and billing.
The plugin never holds a model API key, never holds S3 credentials, and is
fully offline-testable via ``anna-app executa dev --mock-image fixture.jsonl``.

Manifest declares all three host capabilities:

    "host_capabilities": ["llm.image", "llm.image.edit", "host.upload"]

If the user has not toggled the matching grant in their Anna Admin panel,
Nexus rejects the reverse-RPC with IMAGE_NOT_GRANTED (-32101) /
UPLOAD_NOT_GRANTED (-32201) and the tool surfaces that error verbatim.

Run locally with anna-app-cli:

    anna-app executa dev --dir . \\
        --mock-image fixtures/image.jsonl \\
        --mock-upload fixtures/upload.jsonl

Or against a real Nexus account:

    anna-app login --host https://nexus.example.com
    anna-app executa dev --dir . --app-slug my-app
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

# Allow direct execution from a fresh checkout: fall back to the in-repo SDK.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[3] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    HostUploadClient,
    ImageClient,
    ImageError,
    PROTOCOL_VERSION_V2,
    UploadError,
)

# ─── Manifest ────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Image Poster",
    "version": "0.1.0",
    "description": "Generate (and optionally edit + re-upload) a poster image via host LLM image services.",
    "author": "Anna Developer",
    # NEW in v2 — declare every reverse capability used by the plugin.
    # Without these entries, Nexus refuses the matching reverse-RPC with
    # NOT_NEGOTIATED (-32107 for image, -32210 for upload).
    "host_capabilities": ["llm.image", "llm.image.edit", "host.upload"],
    "tools": [
        {
            "name": "poster_create",
            "description": "Generate a poster image for `topic` in the given `style`.",
            "parameters": [
                {"name": "topic", "type": "string", "description": "Subject of the poster", "required": True},
                {"name": "style", "type": "string", "description": "Visual style hint (e.g. 'art-deco', 'cyberpunk')", "required": False, "default": "modern minimalist"},
                {"name": "size", "type": "string", "description": "Image size, e.g. '1024x1024'", "required": False, "default": "1024x1024"},
            ],
        },
        {
            "name": "poster_restyle",
            "description": "Take an existing image URL and apply a new style via image/edit.",
            "parameters": [
                {"name": "image_url", "type": "string", "description": "Image to restyle", "required": True},
                {"name": "style", "type": "string", "description": "Target style", "required": True},
            ],
        },
        {
            "name": "poster_persist",
            "description": "Download an image URL and re-upload it to host storage so the user can attach it.",
            "parameters": [
                {"name": "image_url", "type": "string", "description": "URL to persist", "required": True},
                {"name": "filename", "type": "string", "description": "Filename to assign", "required": False, "default": "poster.png"},
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ─── Reverse-RPC client plumbing ─────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


image = ImageClient(write_frame=_write_frame)
host_upload = HostUploadClient(write_frame=_write_frame)


# ─── Tool implementations ───────────────────────────────────────────


async def _poster_create(topic: str, style: str = "modern minimalist", size: str = "1024x1024", *, invoke_id: str) -> dict:
    if not topic or not topic.strip():
        return {"images": [], "note": "empty topic"}
    prompt = (
        f"A {style} poster about {topic}. High contrast, bold typography, "
        "single focal subject, suitable as a 1024x1024 social-media share."
    )
    result = await image.generate(
        prompt=prompt,
        n=1,
        size=size,
        metadata={"executa_invoke_id": invoke_id, "tool": "poster_create"},
        timeout=120.0,
    )
    return {
        "images": result.get("images", []),
        "model": result.get("model"),
        "quota_used": result.get("quota_used"),
    }


async def _poster_restyle(image_url: str, style: str, *, invoke_id: str) -> dict:
    result = await image.edit(
        image_url=image_url,
        prompt=f"Restyle this poster in a {style} aesthetic. Preserve composition.",
        n=1,
        metadata={"executa_invoke_id": invoke_id, "tool": "poster_restyle"},
        timeout=120.0,
    )
    return {
        "images": result.get("images", []),
        "model": result.get("model"),
    }


async def _poster_persist(image_url: str, filename: str = "poster.png", *, invoke_id: str) -> dict:
    """Download `image_url` and re-upload to host storage via host/uploadFile."""
    # Best-effort scheme guard: only http(s) and data: URIs.
    scheme = urlparse(image_url).scheme.lower()
    if scheme not in ("http", "https", "data"):
        raise ValueError(f"unsupported URL scheme: {scheme}")

    # Synchronous fetch is fine here — invoke is already off the stdin
    # thread and the worker pool isolates it.
    with urlopen(image_url, timeout=30) as resp:  # noqa: S310 — scheme guarded above
        body = resp.read()
        mime = resp.headers.get("content-type", "image/png").split(";")[0].strip()

    # 8 MB inline cap (mirrors SDK MAX_INLINE_BYTES; larger payloads should
    # use negotiate + PUT + confirm — see HostUploadClient.negotiate).
    if len(body) > 8 * 1024 * 1024:
        return {
            "ok": False,
            "error": "image exceeds 8 MB inline cap; use negotiate+confirm flow",
            "size_bytes": len(body),
        }

    result = await host_upload.upload_inline(
        filename=filename,
        mime_type=mime,
        content=body,
        purpose="user_artifact",
        metadata={"executa_invoke_id": invoke_id, "tool": "poster_persist"},
        timeout=60.0,
    )
    return {
        "ok": True,
        "download_url": result.get("download_url"),
        "r2_key": result.get("r2_key"),
        "size_bytes": result.get("size_bytes", len(body)),
        "expires_at": result.get("expires_at"),
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
        image.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "image/generate requires Executa protocol 2.0"
        )
        host_upload.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "host/uploadFile requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": (
                {"image": {}, "image.edit": {}, "upload": {}}
                if proto == PROTOCOL_VERSION_V2
                else {}
            ),
            "capabilities": {},
        },
    )


def _handle_describe(req_id) -> dict:
    # describe MUST return the bare manifest — Matrix's ToolManifest.from_dict
    # reads name/tools off the result directly. See
    # /memories/executa-describe-result-shape.md.
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


_TOOLS = {
    "poster_create": _poster_create,
    "poster_restyle": _poster_restyle,
    "poster_persist": _poster_persist,
}


def _handle_invoke(req_id, params: dict) -> dict:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    invoke_id = params.get("invoke_id") or ""

    fn = _TOOLS.get(tool)
    if fn is None:
        return _make_response(
            req_id, error={"code": -32601, "message": f"Unknown tool: {tool}"}
        )

    fut = asyncio.run_coroutine_threadsafe(fn(invoke_id=invoke_id, **args), _loop)
    try:
        data = fut.result(timeout=180.0)
    except ImageError as e:
        return _make_response(req_id, error={"code": e.code, "message": e.message, "data": e.data})
    except UploadError as e:
        return _make_response(req_id, error={"code": e.code, "message": e.message, "data": e.data})
    except ValueError as e:
        return _make_response(req_id, error={"code": -32602, "message": str(e)})
    except Exception as e:  # noqa: BLE001
        return _make_response(req_id, error={"code": -32603, "message": f"Tool execution failed: {e}"})
    # invoke MUST be wrapped {success, data} — see
    # /memories/executa-invoke-result-shape.md.
    return _make_response(req_id, result={"success": True, "tool": tool, "data": data})


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply → resolve a pending image/upload future.
    if "method" not in msg:
        if not image.dispatch_response(msg) and not host_upload.dispatch_response(msg):
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
    print("🖼  image-poster plugin started", file=sys.stderr)
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
