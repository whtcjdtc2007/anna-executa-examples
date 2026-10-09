"""Anna Executa Python SDK — Media (Video generation / TTS) support.

`MediaClient` lets an Executa plugin issue reverse JSON-RPC requests for
``video/generate`` / ``video/get_job`` / ``video/cancel_job`` and
``audio/speak`` to its host Agent — the Agent proxies to Nexus's
``/api/v1/copilot/media/*`` endpoints using a short-lived ``media_token``
(aud=executa-media) the host minted at invoke time.

Video generation is an **async job**: ``video_generate`` returns a
``jobId`` immediately; the plugin polls ``video_get_job`` for the terminal
state (the :meth:`MediaClient.generate_await` sugar does this for you).
Costs are pre-charged from the estimate and fully refunded on
failure / cancel / expiry. ``audio/speak`` is synchronous TTS.

The plugin never sees the provider API key or the user's billing context.
Both capabilities are gated by the user's ``media_grant`` block on
UserExecuta custom_config AND the manifest ``host_capabilities``
declaration (``llm.video`` / ``llm.audio.speak``).

Wire protocol (Plugin → Agent → Nexus):

    Plugin (us)                         Agent (host)                  Nexus
    ─────────────────────────────────────────────────────────────────────────
    invoke(req_id=42, …)         ◄── (host called us)
    video/generate(req_id=A, …)  ──► POST /copilot/media/video/generate
                                       header: ******
                                     ◄── 200 {jobId, state:"queued", …}
    video/get_job(req_id=B, …)   ──► POST /copilot/media/video/get_job
                                     ◄── 200 {state:"succeeded", result:{url,…}}
    ◄── result | error           ──┘
    invoke result(req_id=42)     ──► (we finish original tool)

Error codes — keep in sync with ``matrix/src/executa/protocol.py`` and
``matrix-nexus/src/services/app_media_facade.py``::

    VIDEO_ERR_NOT_GRANTED          = -32120
    VIDEO_ERR_QUOTA_EXCEEDED       = -32121
    VIDEO_ERR_INVALID_REQUEST      = -32122
    VIDEO_ERR_PROVIDER_ERROR       = -32123
    VIDEO_ERR_MODEL_UNAVAILABLE    = -32124
    VIDEO_ERR_JOB_NOT_FOUND        = -32125
    VIDEO_ERR_JOB_NOT_CANCELLABLE  = -32126
    VIDEO_ERR_CONCURRENCY_EXCEEDED = -32127
    VIDEO_ERR_CONTENT_REJECTED     = -32128
    AUDIO_ERR_NOT_GRANTED          = -32140
    AUDIO_ERR_QUOTA_EXCEEDED       = -32141
    AUDIO_ERR_TEXT_TOO_LONG        = -32142
    AUDIO_ERR_TOO_LONG             = -32143
    AUDIO_ERR_PROVIDER_ERROR       = -32144
    AUDIO_ERR_VOICE_INVALID        = -32145
    AUDIO_ERR_MODEL_UNAVAILABLE    = -32146
    AUDIO_ERR_CONTENT_REJECTED     = -32147
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .context import attach_invoke_context
from .sampling import _write_frame

# ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

METHOD_VIDEO_GENERATE = "video/generate"
METHOD_VIDEO_GET_JOB = "video/get_job"
METHOD_VIDEO_CANCEL_JOB = "video/cancel_job"
METHOD_AUDIO_SPEAK = "audio/speak"

# ─── Error codes ──────────────────────────────────────────────────────

VIDEO_ERR_NOT_GRANTED = -32120
VIDEO_ERR_QUOTA_EXCEEDED = -32121
VIDEO_ERR_INVALID_REQUEST = -32122
VIDEO_ERR_PROVIDER_ERROR = -32123
VIDEO_ERR_MODEL_UNAVAILABLE = -32124
VIDEO_ERR_JOB_NOT_FOUND = -32125
VIDEO_ERR_JOB_NOT_CANCELLABLE = -32126
VIDEO_ERR_CONCURRENCY_EXCEEDED = -32127
VIDEO_ERR_CONTENT_REJECTED = -32128
AUDIO_ERR_NOT_GRANTED = -32140
AUDIO_ERR_QUOTA_EXCEEDED = -32141
AUDIO_ERR_TEXT_TOO_LONG = -32142
AUDIO_ERR_TOO_LONG = -32143
AUDIO_ERR_PROVIDER_ERROR = -32144
AUDIO_ERR_VOICE_INVALID = -32145
AUDIO_ERR_MODEL_UNAVAILABLE = -32146
AUDIO_ERR_CONTENT_REJECTED = -32147

_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})


class MediaError(Exception):
    """Wraps a JSON-RPC error returned by the host for media reverse RPCs."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


class VideoJobTimeout(MediaError):
    """`generate_await` wall clock elapsed; the job may still be rendering.

    Carries ``job_id`` — recover the result later via
    :meth:`MediaClient.video_get_job`.
    """

    def __init__(self, job_id: str, waited_s: float):
        super().__init__(
            VIDEO_ERR_PROVIDER_ERROR,
            f"video job {job_id} still rendering after {waited_s:.0f}s",
            data={"jobId": job_id},
        )
        self.job_id = job_id


# ─── Internal plumbing ────────────────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class MediaClient:
    """Reverse-RPC client for ``video/*`` and ``audio/speak``.

    Usage::

        from executa_sdk import MediaClient, MediaError

        media = MediaClient()

        # One-shot sugar: submit + poll until the video is ready
        job = await media.generate_await(
            prompt="Aerial drone shot over a misty pine forest at sunrise",
            duration_sec=5,
            resolution="768p",
        )
        print(job["result"]["url"])          # presigned GET, ~30 min TTL

        # Sync TTS
        speech = await media.speak(text="Welcome!", voice="Cherry")
        print(speech["url"])

    Like ``StorageClient``, register a stdin reader that calls
    :meth:`dispatch_response` (or use :func:`make_response_router`).
    """

    DEFAULT_TIMEOUT = 120.0
    # generate_await 轮询:5s 间隔,默认最多等 9 分钟(job deadline 30 min)
    DEFAULT_POLL_INTERVAL_S = 5.0
    DEFAULT_AWAIT_TIMEOUT_S = 540.0

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
        """Mark media namespace as unavailable (host did not negotiate
        ``llm.video`` / ``llm.audio.speak``)."""
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
                    MediaError(
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

    # — public API: video —

    async def video_generate(
        self,
        *,
        prompt: str,
        image_url: Optional[str] = None,
        duration_sec: Optional[int] = None,
        resolution: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        model: Optional[str] = None,
        client_tag: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Submit an async video generation job.

        With ``image_url`` the clip is driven by that image (image-to-video;
        ``aspect_ratio`` is then ignored and follows the image).

        Returns immediately::

            {"jobId": "vjob_…", "state": "queued",
             "estimatedCostCU": 74, "deadlineAt": "…"}

        Costs are pre-charged from ``estimatedCostCU`` and fully refunded
        on failure / cancel / expiry. Raises :class:`MediaError`.
        """
        params: Dict[str, Any] = {"prompt": prompt}
        if image_url is not None:
            params["imageUrl"] = image_url
        if duration_sec is not None:
            params["durationSec"] = int(duration_sec)
        if resolution is not None:
            params["resolution"] = resolution
        if aspect_ratio is not None:
            params["aspectRatio"] = aspect_ratio
        if generate_audio is not None:
            params["generateAudio"] = bool(generate_audio)
        if model is not None:
            params["model"] = model
        if client_tag is not None:
            params["clientTag"] = client_tag
        return await self._call(METHOD_VIDEO_GENERATE, params, timeout)

    async def video_get_job(
        self, job_id: str, *, timeout: float = 60.0
    ) -> dict:
        """Authoritative job snapshot. On ``succeeded`` the ``result.url``
        presigned GET is freshly re-signed (~30 min TTL) — call again after
        expiry to get a new URL."""
        return await self._call(METHOD_VIDEO_GET_JOB, {"jobId": job_id}, timeout)

    async def video_cancel_job(
        self, job_id: str, *, timeout: float = 60.0
    ) -> dict:
        """Cancel a job. Queued = guaranteed cancel + full refund; running
        cancel is best-effort (provider may refuse — the job then runs to
        terminal and is billed normally)."""
        return await self._call(METHOD_VIDEO_CANCEL_JOB, {"jobId": job_id}, timeout)

    async def generate_await(
        self,
        *,
        prompt: str,
        image_url: Optional[str] = None,
        duration_sec: Optional[int] = None,
        resolution: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        model: Optional[str] = None,
        client_tag: Optional[str] = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        await_timeout_s: float = DEFAULT_AWAIT_TIMEOUT_S,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """Submit + poll until the job reaches a terminal state.

        Returns the terminal job view on success (``result.url`` = playable
        presigned GET). Raises :class:`MediaError` on failed / cancelled /
        expired jobs and :class:`VideoJobTimeout` (carrying ``job_id``)
        when ``await_timeout_s`` elapses first — the job keeps rendering
        server-side and can be recovered via :meth:`video_get_job`.
        """
        created = await self.video_generate(
            prompt=prompt,
            image_url=image_url,
            duration_sec=duration_sec,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            model=model,
            client_tag=client_tag,
        )
        job_id = created["jobId"]
        waited = 0.0
        while waited < await_timeout_s:
            await asyncio.sleep(poll_interval_s)
            waited += poll_interval_s
            view = await self.video_get_job(job_id)
            if on_progress is not None and view.get("progress"):
                try:
                    on_progress(view["progress"])
                except Exception:
                    pass
            state = view.get("state")
            if state in _TERMINAL_JOB_STATES:
                if state == "succeeded":
                    return view
                err = view.get("error") or {}
                raise MediaError(
                    VIDEO_ERR_PROVIDER_ERROR,
                    f"video job {job_id} ended as {state}: "
                    f"{err.get('message') or err.get('code') or 'unknown'}",
                    data={"jobId": job_id, "state": state},
                )
        raise VideoJobTimeout(job_id, waited)

    # — public API: audio —

    async def speak(
        self,
        *,
        text: str,
        voice: Optional[str] = None,
        language: Optional[str] = None,
        timestamps: Optional[bool] = None,
        format: Optional[str] = None,
        speed: Optional[float] = None,
        delivery: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Synchronous TTS (``audio/speak``; EXECUTA_TTS billing).

        Returns::

            {"url": "https://r2…(presigned ~30min)", "mimeType": "audio/mpeg",
             "charCount": 28, "billedCostCU": 3,
             "model": "<resolved model>", "voice": "<resolved voice>"}

        With ``delivery="inline"`` small artifacts come back as
        ``audioBase64`` instead of ``url``. Raises :class:`MediaError`;
        ``-32145`` carries the model's valid voice list in ``.data.allowed``.
        """
        params: Dict[str, Any] = {"text": text}
        if voice is not None:
            params["voice"] = voice
        if language is not None:
            params["language"] = language
        if timestamps is not None:
            params["timestamps"] = bool(timestamps)
        if format is not None:
            params["format"] = format
        if speed is not None:
            params["speed"] = float(speed)
        if delivery is not None:
            params["delivery"] = delivery
        if model is not None:
            params["model"] = model
        return await self._call(METHOD_AUDIO_SPEAK, params, timeout)

    # — internal —

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        if self._disabled_reason:
            code = (
                AUDIO_ERR_NOT_GRANTED
                if method == METHOD_AUDIO_SPEAK
                else VIDEO_ERR_NOT_GRANTED
            )
            raise MediaError(code, self._disabled_reason)
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
            raise MediaError(
                VIDEO_ERR_PROVIDER_ERROR,
                f"{method} timed out after {timeout}s",
            )


__all__ = [
    "MediaClient",
    "MediaError",
    "VideoJobTimeout",
    "METHOD_VIDEO_GENERATE",
    "METHOD_VIDEO_GET_JOB",
    "METHOD_VIDEO_CANCEL_JOB",
    "METHOD_AUDIO_SPEAK",
    "VIDEO_ERR_NOT_GRANTED",
    "VIDEO_ERR_QUOTA_EXCEEDED",
    "VIDEO_ERR_INVALID_REQUEST",
    "VIDEO_ERR_PROVIDER_ERROR",
    "VIDEO_ERR_MODEL_UNAVAILABLE",
    "VIDEO_ERR_JOB_NOT_FOUND",
    "VIDEO_ERR_JOB_NOT_CANCELLABLE",
    "VIDEO_ERR_CONCURRENCY_EXCEEDED",
    "VIDEO_ERR_CONTENT_REJECTED",
    "AUDIO_ERR_NOT_GRANTED",
    "AUDIO_ERR_QUOTA_EXCEEDED",
    "AUDIO_ERR_TEXT_TOO_LONG",
    "AUDIO_ERR_TOO_LONG",
    "AUDIO_ERR_PROVIDER_ERROR",
    "AUDIO_ERR_VOICE_INVALID",
    "AUDIO_ERR_MODEL_UNAVAILABLE",
    "AUDIO_ERR_CONTENT_REJECTED",
]
