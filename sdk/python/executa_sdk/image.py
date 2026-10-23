"""Anna Executa Python SDK — Image (Generate / Edit) support.

`ImageClient` lets an Executa plugin issue reverse JSON-RPC requests for
``image/generate`` and ``image/edit`` (synchronous) and the async-job family
``image/{generate_async, edit_async, get_job, cancel_job, resign}`` to its host
Agent — the Agent proxies to Nexus's `/api/v1/copilot/image/*` endpoints using a
short-lived ``image_token`` (aud=executa-image) the host minted at invoke time.

Async jobs (``generate_async`` / ``edit_async`` → ``ijob_…``) mirror the music
job surface: submit returns immediately, the plugin polls ``get_job`` (or uses
``generate_await`` / ``edit_await``), ``result.images[].url`` is re-signed on
every ``get_job``, and ``resign`` re-signs any already-paid artifact by
``r2Key`` for up to 7 days. Prefer jobs for 2K/4K or ``quality=high`` renders
(60–120 s) so the synchronous call does not eat the invoke timeout budget.

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
    IMAGE_ERR_JOB_NOT_FOUND        = -32113
    IMAGE_ERR_JOB_NOT_CANCELLABLE  = -32114
    IMAGE_ERR_CONTENT_REJECTED     = -32115
    IMAGE_ERR_JOB_EXPIRED          = -32116
    IMAGE_ERR_ASSET_NOT_FOUND      = -32117
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

from .context import attach_invoke_context
from .sampling import _write_frame


# ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

METHOD_IMAGE_GENERATE = "image/generate"
METHOD_IMAGE_EDIT = "image/edit"
# Async jobs + re-sign (platform-media-generation-image-jobs.md §11)
METHOD_IMAGE_GENERATE_ASYNC = "image/generate_async"
METHOD_IMAGE_EDIT_ASYNC = "image/edit_async"
METHOD_IMAGE_GET_JOB = "image/get_job"
METHOD_IMAGE_CANCEL_JOB = "image/cancel_job"
METHOD_IMAGE_RESIGN = "image/resign"

# Polling defaults for ``generate_await`` / ``edit_await`` (same as music).
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_AWAIT_TIMEOUT_S = 540.0
_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})


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
IMAGE_ERR_JOB_NOT_FOUND = -32113
IMAGE_ERR_JOB_NOT_CANCELLABLE = -32114
IMAGE_ERR_CONTENT_REJECTED = -32115
IMAGE_ERR_JOB_EXPIRED = -32116
IMAGE_ERR_ASSET_NOT_FOUND = -32117
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


class ImageJobTimeout(TimeoutError):
    """``generate_await`` / ``edit_await`` wall clock elapsed; the job may still
    be rendering. Recover it with :meth:`ImageClient.get_job` (e.g. in the next
    invoke — return ``job_id`` as the tool result)."""

    def __init__(self, job_id: str, waited_s: float):
        super().__init__(f"image job {job_id} still rendering after {waited_s:.1f}s")
        self.job_id = job_id
        self.waited_s = waited_s


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
        quality: Optional[str] = None,
        resolution: Optional[str] = None,
        output_format: Optional[str] = None,
        enable_web_search: Optional[bool] = None,
        thinking_level: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Generate ``n`` images from a text ``prompt``.

        Advanced options (host validates; unsupported models silently
        ignore them — no extra charge is billed in that case):

        - ``quality``: ``"low" | "medium" | "high"`` — GPT-Image-family
          models only; strongly affects per-image cost.
        - ``resolution``: ``"0.5K" | "1K" | "2K" | "4K"`` — Nano-Banana-family
          models; billed at 0.75x / 1x / 1.5x / 2x the base rate.
        - ``output_format``: ``"png" | "jpeg" | "webp"``.
        - ``enable_web_search``: ground the generation in live web results
          (Nano Banana 2; small surcharge per call).
        - ``thinking_level``: ``"minimal" | "high"`` (Nano Banana 2;
          ``high`` adds a small surcharge).

        Returns the host's response dict, typically::

            {
              "images": [{"url": "https://r2.example.com/...", "mimeType": "image/png"}, ...],
              "model": "fal-ai/nano-banana-2",
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
        if quality is not None:
            params["quality"] = quality
        if resolution is not None:
            params["resolution"] = resolution
        if output_format is not None:
            params["output_format"] = output_format
        if enable_web_search is not None:
            params["enable_web_search"] = bool(enable_web_search)
        if thinking_level is not None:
            params["thinking_level"] = thinking_level
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
        quality: Optional[str] = None,
        resolution: Optional[str] = None,
        output_format: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Edit a source ``image_url`` according to a text ``prompt``.

        ``mask_url`` is optional. Without it, the provider does whole-image
        edit; with it, only masked pixels change. Mask must be a 1-channel
        PNG with the same dimensions as ``image_url`` (white = edit).
        Mask editing requires a model that supports it (e.g. GPT Image 2);
        otherwise the host rejects with -32312.

        ``quality`` / ``resolution`` / ``output_format`` follow the same
        semantics as :meth:`generate`.

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
        if quality is not None:
            params["quality"] = quality
        if resolution is not None:
            params["resolution"] = resolution
        if output_format is not None:
            params["output_format"] = output_format
        return await self._call(METHOD_IMAGE_EDIT, params, timeout)

    # — async jobs + re-sign (image-jobs §11.5) —

    @staticmethod
    def _job_params(
        *,
        prompt: str,
        n: int,
        size: Optional[str],
        aspect_ratio: Optional[str],
        model: Optional[str],
        params: Optional[dict],
        fit: Optional[str],
        client_tag: Optional[str],
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {"prompt": prompt, "n": int(n)}
        if size is not None:
            out["size"] = size
        if aspect_ratio is not None:
            out["aspect_ratio"] = aspect_ratio
        if model is not None:
            out["model"] = model
        if params:
            out["params"] = dict(params)
        if fit is not None:
            out["fit"] = fit
        if client_tag is not None:
            out["clientTag"] = client_tag
        if idempotency_key is not None:
            out["idempotencyKey"] = idempotency_key
        return out

    async def generate_async(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        model: Optional[str] = None,
        params: Optional[dict] = None,
        fit: str = "nearest",
        reference_image_urls: Optional[List[str]] = None,
        client_tag: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: float = 60.0,
    ) -> dict:
        """Submit an async text-to-image / reference-image job.

        Returns ``ImageJobCreated``::

            {"jobId": "ijob_…", "kind": "image", "state": "queued",
             "estimatedCostCU": 24, "deadlineAt": "…", "idempotentReplay": false}

        One job = one user action = ``n`` images (billed up-front, settled on
        actual output, refunded on failure / cancel). Pass ``idempotency_key``
        (e.g. ``f"{tool_invoke_id}:{attempt}"``) so retries never double-charge.
        Per-invoke ``max_images`` is reserved by ``n`` at submit time and is not
        released when the job later fails.
        """
        p = self._job_params(
            prompt=prompt,
            n=n,
            size=size,
            aspect_ratio=aspect_ratio,
            model=model,
            params=params,
            fit=fit,
            client_tag=client_tag,
            idempotency_key=idempotency_key,
        )
        if reference_image_urls:
            p["reference_image_urls"] = list(reference_image_urls)
        return await self._call(METHOD_IMAGE_GENERATE_ASYNC, p, timeout)

    async def edit_async(
        self,
        image_url: str,
        prompt: str,
        *,
        n: int = 1,
        size: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        model: Optional[str] = None,
        params: Optional[dict] = None,
        fit: str = "nearest",
        mask_url: Optional[str] = None,
        client_tag: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout: float = 60.0,
    ) -> dict:
        """Submit an async edit job (optionally mask-based; -32312 when the
        resolved model cannot mask). Returns ``ImageJobCreated``."""
        p = self._job_params(
            prompt=prompt,
            n=n,
            size=size,
            aspect_ratio=aspect_ratio,
            model=model,
            params=params,
            fit=fit,
            client_tag=client_tag,
            idempotency_key=idempotency_key,
        )
        p["image_url"] = image_url
        if mask_url is not None:
            p["mask_url"] = mask_url
        return await self._call(METHOD_IMAGE_EDIT_ASYNC, p, timeout)

    async def get_job(self, job_id: str, *, timeout: float = 60.0) -> dict:
        """Authoritative job snapshot (``ImageJobView``); ``result.images[].url``
        is re-signed on every call (~30 min TTL). Only jobs submitted by this
        Executa are visible (App-side ``ijob_`` → -32113)."""
        return await self._call(METHOD_IMAGE_GET_JOB, {"jobId": job_id}, timeout)

    async def cancel_job(self, job_id: str, *, timeout: float = 60.0) -> dict:
        """Cancel a job: ``queued`` = guaranteed + full refund; ``running`` =
        best-effort (provider may refuse → job keeps running, no refund)."""
        return await self._call(METHOD_IMAGE_CANCEL_JOB, {"jobId": job_id}, timeout)

    async def resign(self, r2_keys: List[str], *, timeout: float = 60.0) -> dict:
        """Re-sign already-paid artifacts by ``r2Key`` (1–20; from a sync
        ``generate`` / ``edit`` response or a job view). Returns
        ``{"urls": [{"r2Key", "url", "expiresIn"}]}`` in input order; any key
        that is not yours → -32117 for the whole call."""
        return await self._call(METHOD_IMAGE_RESIGN, {"r2Keys": list(r2_keys)}, timeout)

    async def _await_job(
        self,
        created: dict,
        *,
        poll_interval_s: float,
        await_timeout_s: float,
        on_progress: Optional[Callable[[dict], None]],
    ) -> dict:
        job_id = created["jobId"]

        def _terminal_error(view: dict) -> ImageError:
            err = view.get("error") or {}
            code_name = str(err.get("code") or "")
            code = (
                IMAGE_ERR_CONTENT_REJECTED
                if code_name == "IMAGE_CONTENT_REJECTED"
                else IMAGE_ERR_JOB_EXPIRED
                if code_name == "IMAGE_JOB_EXPIRED"
                else IMAGE_ERR_PROVIDER_ERROR
            )
            return ImageError(
                code,
                f"image job {job_id} ended as {view.get('state')}: "
                f"{err.get('message') or code_name or 'unknown'}",
                data={"jobId": job_id, "state": view.get("state"), "error": err},
            )

        if created.get("state") in _TERMINAL_JOB_STATES:
            if created.get("state") == "succeeded":
                return created
            raise _terminal_error(created)
        waited = 0.0
        while waited < await_timeout_s:
            await asyncio.sleep(poll_interval_s)
            waited += poll_interval_s
            view = await self.get_job(job_id)
            if on_progress is not None and view.get("progress"):
                try:
                    on_progress(view["progress"])
                except Exception:
                    pass
            state = view.get("state")
            if state in _TERMINAL_JOB_STATES:
                if state == "succeeded":
                    return view
                raise _terminal_error(view)
        raise ImageJobTimeout(job_id, waited)

    async def generate_await(
        self,
        prompt: str,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        await_timeout_s: float = DEFAULT_AWAIT_TIMEOUT_S,
        on_progress: Optional[Callable[[dict], None]] = None,
        **kwargs: Any,
    ) -> dict:
        """``generate_async`` + poll until terminal (pure polling; same cadence as
        ``music_generate_await``: 5 s / 9 min). Returns the terminal job view;
        raises :class:`ImageError` on failed / cancelled / expired and
        :class:`ImageJobTimeout` (carrying ``job_id``) when the wall clock elapses
        first. Keep ``await_timeout_s`` within the invoke budget — for longer
        renders return ``jobId`` as the invoke result and resume with ``get_job``."""
        created = await self.generate_async(prompt, **kwargs)
        return await self._await_job(
            created,
            poll_interval_s=poll_interval_s,
            await_timeout_s=await_timeout_s,
            on_progress=on_progress,
        )

    async def edit_await(
        self,
        image_url: str,
        prompt: str,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        await_timeout_s: float = DEFAULT_AWAIT_TIMEOUT_S,
        on_progress: Optional[Callable[[dict], None]] = None,
        **kwargs: Any,
    ) -> dict:
        """``edit_async`` + poll until terminal (see :meth:`generate_await`)."""
        created = await self.edit_async(image_url, prompt, **kwargs)
        return await self._await_job(
            created,
            poll_interval_s=poll_interval_s,
            await_timeout_s=await_timeout_s,
            on_progress=on_progress,
        )

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
            "params": attach_invoke_context(params),
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
    "ImageJobTimeout",
    "METHOD_IMAGE_GENERATE",
    "METHOD_IMAGE_EDIT",
    "METHOD_IMAGE_GENERATE_ASYNC",
    "METHOD_IMAGE_EDIT_ASYNC",
    "METHOD_IMAGE_GET_JOB",
    "METHOD_IMAGE_CANCEL_JOB",
    "METHOD_IMAGE_RESIGN",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_AWAIT_TIMEOUT_S",
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
    "IMAGE_ERR_JOB_NOT_FOUND",
    "IMAGE_ERR_JOB_NOT_CANCELLABLE",
    "IMAGE_ERR_CONTENT_REJECTED",
    "IMAGE_ERR_JOB_EXPIRED",
    "IMAGE_ERR_ASSET_NOT_FOUND",
    "IMAGE_ERR_EDIT_NOT_SUPPORTED",
    "IMAGE_ERR_MASK_UNSUPPORTED",
    "IMAGE_ERR_N_UNSUPPORTED",
    "IMAGE_ERR_REFERENCE_FETCH_FAILED",
]
