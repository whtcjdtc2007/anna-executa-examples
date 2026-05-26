"""Anna Executa Python SDK — Image (Generate / Edit) support.

`ImageClient` lets an Executa plugin issue reverse JSON-RPC requests for
``image/generate`` and ``image/edit`` to its host Agent — the Agent proxies
to Nexus's `/api/v1/copilot/image/{generate,edit}` endpoints using a
short-lived ``image_token`` (aud=executa-image) the host minted at invoke time.

The plugin never sees the LLM API key or the user's billing context — it
only pays in image counts, gated by the user's grant
(``image_grant`` block on UserExecuta custom_config).

Wire protocol (Plugin → Agent → Nexus):

    Plugin (us)                        Agent (host)                   Nexus
    ────────────────────────────────────────────────────────────────────────
    invoke(req_id=42, …)        ◄── (host called us)
    image/generate(req_id=A, …) ──► (we ask host for image)
                                     POST /copilot/image/generate
                                       header: Bearer <image_token>
                                       body: {prompt, n, size, …}
                                     ◄── 200 {images:[{url, …}], ...}
    ◄── result | error          ──┘
    invoke result(req_id=42)    ──► (we finish original tool)

Transport-compatible with :mod:`executa_sdk.sampling` and
:mod:`executa_sdk.storage`: register all clients in one
:func:`make_response_router` call to demultiplex incoming JSON-RPC frames.

Error codes — keep in sync with ``matrix/src/executa/protocol.py``::

    IMAGE_ERR_NOT_GRANTED          = -32101
    IMAGE_ERR_QUOTA_EXCEEDED       = -32102
    IMAGE_ERR_PROVIDER_ERROR       = -32103
    IMAGE_ERR_INVALID_REQUEST      = -32104
    IMAGE_ERR_TIMEOUT              = -32105
    IMAGE_ERR_MAX_IMAGES_EXCEEDED  = -32106
    IMAGE_ERR_NOT_NEGOTIATED       = -32107
    IMAGE_ERR_USER_DENIED          = -32108
    IMAGE_ERR_NO_MODEL_AVAILABLE   = -32109
    IMAGE_ERR_STORAGE_ERROR        = -32110
    IMAGE_ERR_EDIT_NOT_SUPPORTED   = -32311
    IMAGE_ERR_MASK_UNSUPPORTED     = -32312
    IMAGE_ERR_N_UNSUPPORTED        = -32313
    IMAGE_ERR_REFERENCE_FETCH_FAILED = -32314
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .sampling import _write_frame


# ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

METHOD_IMAGE_GENERATE = "image/generate"
METHOD_IMAGE_EDIT = "image/edit"


# ─── Error codes ──────────────────────────────────────────────────────

IMAGE_ERR_NOT_GRANTED = -32101
IMAGE_ERR_QUOTA_EXCEEDED = -32102
IMAGE_ERR_PROVIDER_ERROR = -32103
IMAGE_ERR_INVALID_REQUEST = -32104
IMAGE_ERR_TIMEOUT = -32105
IMAGE_ERR_MAX_IMAGES_EXCEEDED = -32106
IMAGE_ERR_NOT_NEGOTIATED = -32107
IMAGE_ERR_USER_DENIED = -32108
IMAGE_ERR_NO_MODEL_AVAILABLE = -32109
IMAGE_ERR_STORAGE_ERROR = -32110
IMAGE_ERR_EDIT_NOT_SUPPORTED = -32311
IMAGE_ERR_MASK_UNSUPPORTED = -32312
IMAGE_ERR_N_UNSUPPORTED = -32313
IMAGE_ERR_REFERENCE_FETCH_FAILED = -32314


class ImageError(Exception):
    """Wraps a JSON-RPC error returned by the host for ``image/*`` reverse RPCs."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


# ─── Internal plumbing ────────────────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class ImageClient:
    """Reverse-RPC client for ``image/generate`` and ``image/edit``.

    Usage::

        from executa_sdk import ImageClient, ImageError

        image = ImageClient()
        try:
            result = await image.generate(
                prompt="A cyberpunk owl wearing aviator goggles",
                n=2,
                size="1024x1024",
            )
            # result = {
            #   "images": [{"url": "https://...", "mimeType": "image/png"}, ...],
            #   "model": "dall-e-3",
            #   "quota_used": {"image_count": 2, ...},
            # }
            for img in result["images"]:
                print(img["url"])
        except ImageError as e:
            if e.code == IMAGE_ERR_NOT_GRANTED:
                print("User has not granted image generation")
            elif e.code == IMAGE_ERR_QUOTA_EXCEEDED:
                print("User out of image quota")
            else:
                raise

    Like ``StorageClient``, you must register a stdin reader that calls
    :meth:`dispatch_response` (or use :func:`make_response_router`).
    """

    DEFAULT_TIMEOUT = 120.0  # generation can be slow on cold-start providers

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
        """Mark image namespace as unavailable (host did not negotiate ``llm.image``)."""
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
                    ImageError(
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

    async def generate(
        self,
        *,
        prompt: str,
        n: int = 1,
        size: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        model_preferences: Optional[dict] = None,
        metadata: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Generate ``n`` images from a text ``prompt``.

        Returns the host's response dict, typically::

            {
              "images": [{"url": "https://r2.example.com/...", "mimeType": "image/png"}, ...],
              "model": "dall-e-3",
              "quota_used": {"image_count": 2},
            }

        Raises :class:`ImageError` on any host-side failure.
        """
        params: Dict[str, Any] = {"prompt": prompt, "n": int(n)}
        if size is not None:
            params["size"] = size
        if reference_image_urls is not None:
            params["reference_image_urls"] = list(reference_image_urls)
        if model_preferences is not None:
            params["modelPreferences"] = model_preferences
        if metadata is not None:
            params["metadata"] = metadata
        return await self._call(METHOD_IMAGE_GENERATE, params, timeout)

    async def edit(
        self,
        *,
        image_url: str,
        prompt: str,
        mask_url: Optional[str] = None,
        n: int = 1,
        size: Optional[str] = None,
        model_preferences: Optional[dict] = None,
        metadata: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Edit a source ``image_url`` according to a text ``prompt``.

        ``mask_url`` is optional. Without it, the provider does whole-image
        edit; with it, only masked pixels change. Mask must be a 1-channel
        PNG with the same dimensions as ``image_url`` (white = edit).

        Raises :class:`ImageError`; codes -32311/-32312 indicate the
        provider does not support edit or masking.
        """
        params: Dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "n": int(n),
        }
        if mask_url is not None:
            params["mask_url"] = mask_url
        if size is not None:
            params["size"] = size
        if model_preferences is not None:
            params["modelPreferences"] = model_preferences
        if metadata is not None:
            params["metadata"] = metadata
        return await self._call(METHOD_IMAGE_EDIT, params, timeout)

    # — internal —

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        if self._disabled_reason:
            raise ImageError(IMAGE_ERR_NOT_GRANTED, self._disabled_reason)
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
            raise ImageError(
                IMAGE_ERR_TIMEOUT,
                f"{method} timed out after {timeout}s",
            )


__all__ = [
    "ImageClient",
    "ImageError",
    "METHOD_IMAGE_GENERATE",
    "METHOD_IMAGE_EDIT",
    "IMAGE_ERR_NOT_GRANTED",
    "IMAGE_ERR_QUOTA_EXCEEDED",
    "IMAGE_ERR_PROVIDER_ERROR",
    "IMAGE_ERR_INVALID_REQUEST",
    "IMAGE_ERR_TIMEOUT",
    "IMAGE_ERR_MAX_IMAGES_EXCEEDED",
    "IMAGE_ERR_NOT_NEGOTIATED",
    "IMAGE_ERR_USER_DENIED",
    "IMAGE_ERR_NO_MODEL_AVAILABLE",
    "IMAGE_ERR_STORAGE_ERROR",
    "IMAGE_ERR_EDIT_NOT_SUPPORTED",
    "IMAGE_ERR_MASK_UNSUPPORTED",
    "IMAGE_ERR_N_UNSUPPORTED",
    "IMAGE_ERR_REFERENCE_FETCH_FAILED",
]
