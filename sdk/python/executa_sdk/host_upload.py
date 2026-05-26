"""Anna Executa Python SDK — Host Upload (``host/uploadFile``) support.

`HostUploadClient` lets an Executa plugin upload a transient file to Anna's
R2 bucket via a reverse JSON-RPC call. The host gates the upload against
the user's ``upload_grant`` (MIME allowlist, per-file size cap, total bytes
quota) and returns a transient HTTPS URL the plugin can immediately feed
into ``image/edit``, ``sampling/createMessage``, or any other host capability
expecting an HTTPS-reachable asset.

Three modes (selected via ``mode=`` parameter):

* ``"inline"``     — base64-encoded payload, ≤8MB. Simplest; one round-trip.
* ``"negotiate"``  — host returns a presigned PUT URL; plugin uploads bytes
  directly to R2. Best for >8MB or when avoiding base64 overhead.
* ``"confirm"``    — after the plugin PUT to a presigned URL, this confirms
  the upload completed and returns the transient download URL.

Wire protocol (Plugin → Agent → Nexus):

    Plugin                            Agent                            Nexus
    ────────────────────────────────────────────────────────────────────────
    host/uploadFile mode=inline   ──► POST /copilot/upload
                                       header: Bearer <upload_token>
                                       body: {filename, mime_type, content_b64, …}
                                       ◄── 200 {download_url, r2_key, …}
    ◄── result                    ──┘

Transport-compatible with all other reverse-RPC clients — register all in
one :func:`make_response_router` call.

Error codes — keep in sync with ``matrix/src/executa/protocol.py``::

    UPLOAD_ERR_NOT_GRANTED         = -32201
    UPLOAD_ERR_QUOTA_EXCEEDED      = -32202
    UPLOAD_ERR_INVALID_REQUEST     = -32203
    UPLOAD_ERR_TOO_LARGE           = -32204
    UPLOAD_ERR_MIME_REJECTED       = -32205
    UPLOAD_ERR_PURPOSE_REJECTED    = -32206
    UPLOAD_ERR_STORAGE_ERROR       = -32207
    UPLOAD_ERR_TIMEOUT             = -32208
    UPLOAD_ERR_USER_DENIED         = -32209
    UPLOAD_ERR_NOT_NEGOTIATED      = -32210
    UPLOAD_ERR_MAX_FILES_EXCEEDED  = -32211
    UPLOAD_ERR_NOT_FOUND           = -32212
    UPLOAD_ERR_PRESIGN_FAILED      = -32213
"""

from __future__ import annotations

import asyncio
import base64
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .sampling import _write_frame


# ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

METHOD_HOST_UPLOAD_FILE = "host/uploadFile"


# ─── Error codes ──────────────────────────────────────────────────────

UPLOAD_ERR_NOT_GRANTED = -32201
UPLOAD_ERR_QUOTA_EXCEEDED = -32202
UPLOAD_ERR_INVALID_REQUEST = -32203
UPLOAD_ERR_TOO_LARGE = -32204
UPLOAD_ERR_MIME_REJECTED = -32205
UPLOAD_ERR_PURPOSE_REJECTED = -32206
UPLOAD_ERR_STORAGE_ERROR = -32207
UPLOAD_ERR_TIMEOUT = -32208
UPLOAD_ERR_USER_DENIED = -32209
UPLOAD_ERR_NOT_NEGOTIATED = -32210
UPLOAD_ERR_MAX_FILES_EXCEEDED = -32211
UPLOAD_ERR_NOT_FOUND = -32212
UPLOAD_ERR_PRESIGN_FAILED = -32213


class UploadError(Exception):
    """Wraps a JSON-RPC error returned by the host for ``host/uploadFile``."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


# ─── Internal plumbing ────────────────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class HostUploadClient:
    """Reverse-RPC client for ``host/uploadFile``.

    Usage (inline mode, simplest)::

        from executa_sdk import HostUploadClient

        host_upload = HostUploadClient()
        with open("photo.jpg", "rb") as f:
            result = await host_upload.upload_inline(
                filename="photo.jpg",
                mime_type="image/jpeg",
                content=f.read(),         # bytes; we base64-encode
                purpose="image-edit-input",
            )
        # result = {
        #   "download_url": "https://r2.example.com/...",
        #   "r2_key": "exec-uploads/prod/<uuid>/<tool>/<invoke>/...",
        #   "expires_at": "2026-05-26T12:34:56Z",
        #   "size_bytes": 524288,
        # }
        edit_result = await image.edit(
            image_url=result["download_url"],
            prompt="add a halo above the cat",
        )

    Usage (negotiate + confirm, for files >8MB)::

        info = await host_upload.negotiate(
            filename="huge.png",
            mime_type="image/png",
            size_bytes=os.path.getsize("huge.png"),
            purpose="image-edit-input",
        )
        # info = {"put_url": "...", "headers": {...}, "r2_key": "...", "expires_at": "..."}
        async with aiohttp.ClientSession() as s:
            await s.put(info["put_url"], data=open("huge.png", "rb"),
                        headers=info["headers"])
        result = await host_upload.confirm(r2_key=info["r2_key"])
    """

    DEFAULT_TIMEOUT = 120.0
    MAX_INLINE_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        write_frame: Callable[[dict], None] | None = None,
    ) -> None:
        self._write_frame = write_frame or _write_frame
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._disabled_reason: Optional[str] = None

    # — public wiring —

    def disable(self, reason: str) -> None:
        """Mark upload namespace as unavailable (host did not negotiate ``host.upload``)."""
        self._disabled_reason = reason

    def dispatch_response(self, msg: dict) -> bool:
        if not isinstance(msg, dict) or "method" in msg:
            return False
        req_id = msg.get("id")
        if req_id is None:
            return False
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is None:
            return False
        loop = self._loop
        if loop is None or pending.future.done():
            return True

        def _resolve():
            if pending.future.done():
                return
            err = msg.get("error")
            if err:
                pending.future.set_exception(
                    UploadError(
                        code=int(err.get("code", -32603)),
                        message=str(err.get("message", "unknown error")),
                        data=err.get("data"),
                    )
                )
            else:
                pending.future.set_result(msg.get("result") or {})

        try:
            loop.call_soon_threadsafe(_resolve)
        except RuntimeError:
            _resolve()
        return True

    # — public API —

    async def upload_inline(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        purpose: Optional[str] = None,
        metadata: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Upload ``content`` (raw bytes) inline via base64.

        Convenience over the raw protocol — encodes the payload, enforces
        the SDK-local ≤8MB sanity check (host enforces real cap), forwards
        the rest. Returns ``{download_url, r2_key, expires_at, size_bytes}``.
        """
        if len(content) > self.MAX_INLINE_BYTES:
            raise UploadError(
                UPLOAD_ERR_TOO_LARGE,
                f"inline payload {len(content)} bytes exceeds SDK cap "
                f"{self.MAX_INLINE_BYTES} — use negotiate() instead",
            )
        params: Dict[str, Any] = {
            "mode": "inline",
            "filename": filename,
            "mime_type": mime_type,
            "content_b64": base64.b64encode(content).decode("ascii"),
        }
        if purpose is not None:
            params["purpose"] = purpose
        if metadata is not None:
            params["metadata"] = metadata
        return await self._call(METHOD_HOST_UPLOAD_FILE, params, timeout)

    async def negotiate(
        self,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int,
        purpose: Optional[str] = None,
        metadata: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Get a presigned PUT URL for direct upload to R2.

        Returns ``{put_url, headers, r2_key, expires_at}`` — PUT bytes to
        ``put_url`` with ``headers``, then call :meth:`confirm` with the
        returned ``r2_key``.
        """
        params: Dict[str, Any] = {
            "mode": "negotiate",
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": int(size_bytes),
        }
        if purpose is not None:
            params["purpose"] = purpose
        if metadata is not None:
            params["metadata"] = metadata
        return await self._call(METHOD_HOST_UPLOAD_FILE, params, timeout)

    async def confirm(
        self,
        *,
        r2_key: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Confirm a presigned upload completed; returns transient download URL.

        Returns ``{download_url, r2_key, size_bytes, expires_at}``.
        Raises :class:`UploadError` ``-32212`` if the R2 object is missing.
        """
        params: Dict[str, Any] = {"mode": "confirm", "r2_key": r2_key}
        return await self._call(METHOD_HOST_UPLOAD_FILE, params, timeout)

    # — internal —

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        if self._disabled_reason:
            raise UploadError(UPLOAD_ERR_NOT_GRANTED, self._disabled_reason)
        loop = asyncio.get_running_loop()
        self._loop = loop
        req_id = uuid.uuid4().hex
        future: asyncio.Future[dict] = loop.create_future()
        with self._lock:
            self._pending[req_id] = _Pending(future=future)

        envelope = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        try:
            self._write_frame(envelope)
        except Exception:
            with self._lock:
                self._pending.pop(req_id, None)
            raise

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._pending.pop(req_id, None)
            raise UploadError(
                UPLOAD_ERR_TIMEOUT,
                f"{method} timed out after {timeout}s",
            )


__all__ = [
    "HostUploadClient",
    "UploadError",
    "METHOD_HOST_UPLOAD_FILE",
    "UPLOAD_ERR_NOT_GRANTED",
    "UPLOAD_ERR_QUOTA_EXCEEDED",
    "UPLOAD_ERR_INVALID_REQUEST",
    "UPLOAD_ERR_TOO_LARGE",
    "UPLOAD_ERR_MIME_REJECTED",
    "UPLOAD_ERR_PURPOSE_REJECTED",
    "UPLOAD_ERR_STORAGE_ERROR",
    "UPLOAD_ERR_TIMEOUT",
    "UPLOAD_ERR_USER_DENIED",
    "UPLOAD_ERR_NOT_NEGOTIATED",
    "UPLOAD_ERR_MAX_FILES_EXCEEDED",
    "UPLOAD_ERR_NOT_FOUND",
    "UPLOAD_ERR_PRESIGN_FAILED",
]
