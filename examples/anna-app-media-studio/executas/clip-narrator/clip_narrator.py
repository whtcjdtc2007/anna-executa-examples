#!/usr/bin/env python3
"""clip_narrator.py — Executa v2 plugin exercising the media reverse-RPCs.

Demonstrates the plugin-side half of the platform media generation surface
(`platform-media-generation-video-audio.md` §10) in one tool:

1. ``video/generate`` + polling ``video/get_job``   — async video jobs
   (wrapped by ``MediaClient.generate_await``);
2. ``audio/speak``                                  — synchronous TTS;
3. ``music/generate`` + ``music/get_job``           — async music score
   (wrapped by ``MediaClient.music_generate_await``; platform-media-generation-music.md §11.2);
4. ``media_call``                                   — one reverse RPC per
   call, selected by ``op``. It is what the Media Studio page invokes on its
   "Reverse RPC" channel, and it reaches every media method the await
   helpers above do not: ``image/{generate_async,edit_async,get_job,
   cancel_job,resign}``, ``video|music/cancel_job`` and ``audio/transcribe``.

The host (Nexus) owns provider selection, grant checks, quota, and billing;
the Matrix relays each call with the invoke's short-lived ``media_token``
(aud=executa-media). The plugin never holds a provider API key.

Manifest declares both media host capabilities:

    "host_capabilities": ["llm.video", "llm.music", "llm.audio.speak"]

If the user has not toggled the matching ``media_grant`` in their Anna Admin
panel, Nexus rejects the reverse-RPC with VIDEO_NOT_GRANTED (-32120) /
AUDIO_NOT_GRANTED (-32140) and the tool surfaces that error verbatim.

Run locally with anna-app-cli:

    anna-app executa dev --dir .

Or against a real Nexus account:

    anna-app login --host https://nexus.example.com
    anna-app executa dev --dir . --app-slug media-studio
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Allow direct execution from a fresh checkout: fall back to the in-repo SDK.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    for _parent in Path(__file__).resolve().parents:
        _SDK_PATH = _parent / "sdk" / "python"
        if _SDK_PATH.is_dir():
            sys.path.insert(0, str(_SDK_PATH))
            break

from executa_sdk import (  # noqa: E402
    ImageClient,
    ImageError,
    InvokeContext,
    MediaClient,
    MediaError,
    PROTOCOL_VERSION_V2,
    VideoJobTimeout,
    bind_invoke,
)

# ─── Manifest ────────────────────────────────────────────────────────

MEDIA_OPS = ("submit", "get_job", "cancel_job", "resign", "speak", "transcribe")
JOB_KINDS = ("image", "video", "music")

MANIFEST = {
    "display_name": "Clip Narrator",
    "version": "0.1.0",
    "description": "Generate a short video clip and a matching TTS narration via host media services.",
    "author": "Anna Developer",
    # Declare every reverse media capability used by the plugin. Without
    # these entries, Nexus refuses the matching reverse-RPC with
    # NOT_NEGOTIATED / capability errors.
    "host_capabilities": ["llm.video", "llm.music", "llm.audio.speak"],
    "tools": [
        {
            "name": "clip_create",
            "description": "Generate a short video clip for `scene` and await the result.",
            "parameters": [
                {"name": "scene", "type": "string", "description": "What the clip should show", "required": True},
                {"name": "duration_sec", "type": "number", "description": "Clip duration in seconds (model-dependent range)", "required": False, "default": 5},
                {"name": "resolution", "type": "string", "description": "e.g. '720p' / '1080p' (model-dependent)", "required": False, "default": ""},
                {"name": "model", "type": "string", "description": "Optional video-model hint; a miss falls back to the user's preferred/admin-default video model.", "required": False, "default": ""},
            ],
        },
        {
            "name": "clip_narrate",
            "description": "Synthesize TTS narration for `script` and return a playable URL.",
            "parameters": [
                {"name": "script", "type": "string", "description": "Narration text", "required": True},
                {"name": "voice", "type": "string", "description": "Optional voice id from the model's catalog voices", "required": False, "default": ""},
                {"name": "speed", "type": "number", "description": "Speech speed multiplier", "required": False, "default": 1.0},
            ],
        },
        {
            "name": "clip_score",
            "description": "Compose a background score for `scene` via music/generate and await the result. Instrumental by default; pass `lyrics` for a sung track (model must support lyrics — otherwise MUSIC_FEATURE_UNSUPPORTED lists alternatives).",
            "parameters": [
                {"name": "scene", "type": "string", "description": "Mood / genre / tempo description of the score", "required": True},
                {"name": "lyrics", "type": "string", "description": "Optional lyrics (vocal track); leave empty for instrumental", "required": False, "default": ""},
                {"name": "duration_sec", "type": "number", "description": "Only for range-length models (ElevenLabs / Stable Audio / ACE-Step); omit for model-decided length", "required": False, "default": 0},
                {"name": "model", "type": "string", "description": "Optional music-model hint (catalog music_gen)", "required": False, "default": ""},
            ],
        },
        {
            "name": "clip_story",
            "description": "One-shot: generate the clip AND a narration for it (video + audio URLs).",
            "parameters": [
                {"name": "scene", "type": "string", "description": "What the clip should show", "required": True},
                {"name": "script", "type": "string", "description": "Narration text (defaults to a line about the scene)", "required": False, "default": ""},
            ],
        },
        {
            "name": "media_call",
            "description": (
                "One host media interface per call, selected by 'op' "
                "(submit|get_job|cancel_job|resign|speak|transcribe). Mirrors "
                "the page's anna.{image,video,music,audio}.* surface over the "
                "matching reverse RPC. Never waits for a job: 'submit' returns "
                "the created job, 'get_job' one snapshot. Answers in-band: "
                "{ok: true, result} or {ok: false, error: {code, message, data}}."
            ),
            "parameters": [
                {"name": "op", "type": "string", "description": "Interface to call.", "required": True, "enum": list(MEDIA_OPS)},
                {"name": "kind", "type": "string", "description": "op=submit|get_job|cancel_job: which job family.", "required": False, "default": "", "enum": ["", *JOB_KINDS]},
                {"name": "job_id", "type": "string", "description": "op=get_job|cancel_job: the job to read or cancel.", "required": False, "default": ""},
                {"name": "request", "type": "object", "description": "op=submit|speak|transcribe: the arguments of the matching Host API call (e.g. {prompt, durationSec}). An image submit with 'image_url' is an edit. op=resign: {r2Keys: [...]}.", "required": False, "default": {}},
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


media = MediaClient(write_frame=_write_frame)
image = ImageClient(write_frame=_write_frame)


# ─── Tool implementations ───────────────────────────────────────────


def _progress_logger(tool: str):
    def _log(progress: dict) -> None:
        print(f"⏳ {tool}: {progress}", file=sys.stderr)

    return _log


async def _clip_create(
    scene: str,
    duration_sec: float = 5,
    resolution: str = "",
    model: str = "",
    *,
    invoke_id: str,
) -> dict:
    if not scene or not scene.strip():
        return {"ok": False, "note": "empty scene"}
    view = await media.generate_await(
        prompt=f"Short cinematic clip: {scene.strip()}",
        duration_sec=int(duration_sec) if duration_sec else None,
        resolution=resolution.strip() or None,
        model=model.strip() or None,
        client_tag=f"clip-narrator-{invoke_id}",
        on_progress=_progress_logger("clip_create"),
    )
    result = view.get("result") or {}
    return {
        "ok": True,
        "job_id": view.get("jobId"),
        "video_url": result.get("url"),
        "mime_type": result.get("mimeType"),
        "duration_sec": result.get("durationSec"),
        "model": view.get("model"),
        "billed_cost_cu": view.get("billedCostCU"),
        "url_expires_in": result.get("expiresIn"),
    }


async def _clip_narrate(
    script: str, voice: str = "", speed: float = 1.0, *, invoke_id: str
) -> dict:
    out = await media.speak(
        text=script,
        voice=voice.strip() or None,
        speed=speed if speed and speed != 1.0 else None,
        delivery="url",
    )
    return {
        "ok": True,
        "audio_url": out.get("url"),
        "mime_type": out.get("mimeType"),
        "char_count": out.get("charCount"),
        "billed_cost_cu": out.get("billedCostCU"),
    }


async def _clip_score(
    scene: str,
    lyrics: str = "",
    duration_sec: float = 0,
    model: str = "",
    *,
    invoke_id: str,
) -> dict:
    """music/generate → music/get_job (polled). The same invoke's
    ``idempotency_key`` makes a retried tool call return the first job
    instead of charging twice."""
    if not scene or not scene.strip():
        return {"ok": False, "note": "empty scene"}
    try:
        view = await media.music_generate_await(
            prompt=scene.strip(),
            lyrics=lyrics.strip() or None,
            instrumental=not lyrics.strip(),
            duration_sec=int(duration_sec) if duration_sec else None,
            model=model.strip() or None,
            client_tag=f"clip-narrator-score-{invoke_id}",
            idempotency_key=f"clip-score-{invoke_id}"[:64],
            on_progress=_progress_logger("clip_score"),
        )
    except MediaError as e:
        # -32165: surface the alternatives so the agent can pick another model
        return {
            "ok": False,
            "code": e.code,
            "error": e.message,
            "feature": (e.data or {}).get("feature"),
            "alternatives": (e.data or {}).get("alternatives") or [],
        }
    result = view.get("result") or {}
    return {
        "ok": True,
        "job_id": view.get("jobId"),
        "audio_url": result.get("url"),
        "mime_type": result.get("mimeType"),
        "duration_sec": result.get("durationSec"),
        "lyrics": result.get("lyrics"),
        "model": view.get("model"),
        "billed_cost_cu": view.get("billedCostCU"),
        "idempotent_replay": bool(view.get("idempotentReplay")),
        "url_expires_in": result.get("expiresIn"),
    }


async def _clip_story(scene: str, script: str = "", *, invoke_id: str) -> dict:
    """Video + narration in one invoke — both calls share the same
    media_token and are billed independently."""
    narration_text = script.strip() or f"Here is your clip: {scene.strip()}."
    clip, narration = await asyncio.gather(
        _clip_create(scene, invoke_id=invoke_id),
        _clip_narrate(narration_text, invoke_id=invoke_id),
    )
    return {"ok": True, "clip": clip, "narration": narration}


# ─── media_call: one reverse RPC per invoke ──────────────────────────


def _sdk_kwargs(request: dict | None) -> dict:
    """Host API argument names (camelCase) → SDK keyword arguments.

    ``durationSec`` → ``duration_sec``; names that are already snake_case
    (the image wire uses ``aspect_ratio`` / ``reference_image_urls``) pass
    through. An argument the SDK method does not take raises ``TypeError``,
    reported to the caller as invalid params.
    """
    return {
        re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value
        for key, value in (request or {}).items()
    }


async def _submit(kind: str, request: dict | None) -> dict:
    kwargs = _sdk_kwargs(request)
    if kind == "video":
        return await media.video_generate(**kwargs)
    if kind == "music":
        return await media.music_generate(**kwargs)
    if "image_url" in kwargs:
        return await image.edit_async(**kwargs)
    return await image.generate_async(**kwargs)


_GET_JOB = {"image": image.get_job, "video": media.video_get_job, "music": media.music_get_job}
_CANCEL_JOB = {
    "image": image.cancel_job,
    "video": media.video_cancel_job,
    "music": media.music_cancel_job,
}


async def _media_op(op: str, kind: str, job_id: str, request: dict | None) -> dict:
    if op in ("submit", "get_job", "cancel_job") and kind not in JOB_KINDS:
        raise ValueError(f"op={op} needs kind in {JOB_KINDS}, got {kind!r}")
    if op in ("get_job", "cancel_job") and not job_id:
        raise ValueError(f"op={op} needs job_id")
    if op == "submit":
        return await _submit(kind, request)
    if op == "get_job":
        return await _GET_JOB[kind](job_id)
    if op == "cancel_job":
        return await _CANCEL_JOB[kind](job_id)
    if op == "resign":
        return await image.resign(list((request or {}).get("r2Keys") or []))
    if op == "speak":
        return await media.speak(**_sdk_kwargs(request))
    if op == "transcribe":
        kwargs = _sdk_kwargs(request)
        if "audio_base64" in kwargs:
            kwargs["audio_bytes"] = base64.b64decode(kwargs.pop("audio_base64"))
        return await media.transcribe(**kwargs)
    raise ValueError(f"unknown op {op!r} (expected one of {MEDIA_OPS})")


async def _media_call(
    op: str,
    kind: str = "",
    job_id: str = "",
    request: dict | None = None,
    *,
    invoke_id: str,
) -> dict:
    op = str(op or "").strip().lower()
    try:
        # Stamps context.invoke_id on the reverse RPC so the host can tell
        # concurrent invokes apart (the page polls several jobs at once).
        # Bound here, inside the coroutine: contextvars do not follow
        # run_coroutine_threadsafe across threads.
        with bind_invoke(invoke_id or None):
            return {"op": op, "ok": True, "result": await _media_op(op, kind, job_id, request)}
    except (MediaError, ImageError) as e:
        # A host-side refusal is an answer, not a transport failure: return it
        # in-band so its code survives every host's tools.invoke error mapping.
        return {
            "op": op,
            "ok": False,
            "error": {"code": e.code, "message": e.message, "data": e.data},
        }
    except TypeError as e:
        raise ValueError(str(e)) from e


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
        reason = (
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "the media reverse RPCs require Executa protocol 2.0"
        )
        media.disable(reason)
        image.disable(reason)
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": (
                {
                    "video": {},
                    "music": {},
                    "audio.speak": {},
                    "audio.transcribe": {},
                    "image": {},
                    "image.edit": {},
                }
                if proto == PROTOCOL_VERSION_V2
                else {}
            ),
            "capabilities": {},
        },
    )


def _handle_describe(req_id) -> dict:
    # describe MUST return the bare manifest — Matrix's ToolManifest.from_dict
    # reads name/tools off the result directly.
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
    "clip_create": _clip_create,
    "clip_narrate": _clip_narrate,
    "clip_score": _clip_score,
    "clip_story": _clip_story,
    "media_call": _media_call,
}


def _handle_invoke(req_id, params: dict) -> dict:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    invoke_id = InvokeContext.from_params(params).invoke_id or ""

    fn = _TOOLS.get(tool)
    if fn is None:
        return _make_response(
            req_id, error={"code": -32601, "message": f"Unknown tool: {tool}"}
        )

    fut = asyncio.run_coroutine_threadsafe(fn(invoke_id=invoke_id, **args), _loop)
    try:
        # Video jobs can render for minutes — give the invoke headroom
        # beyond the SDK's own await timeout (default 9 min).
        data = fut.result(timeout=630.0)
    except VideoJobTimeout as e:
        return _make_response(
            req_id,
            error={
                "code": e.code,
                "message": f"{e.message} — recover it later via video/get_job",
                "data": e.data,
            },
        )
    except (MediaError, ImageError) as e:
        return _make_response(req_id, error={"code": e.code, "message": e.message, "data": e.data})
    except ValueError as e:
        return _make_response(req_id, error={"code": -32602, "message": str(e)})
    except Exception as e:  # noqa: BLE001
        return _make_response(req_id, error={"code": -32603, "message": f"Tool execution failed: {e}"})
    # invoke MUST be wrapped {success, data}.
    return _make_response(req_id, result={"success": True, "tool": tool, "data": data})


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply → resolve a pending media future.
    if "method" not in msg:
        if not media.dispatch_response(msg) and not image.dispatch_response(msg):
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
    print("🎬 clip-narrator plugin started", file=sys.stderr)
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
