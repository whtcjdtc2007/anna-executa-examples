#!/usr/bin/env python3
"""background_check_plugin.py — a backend-owned task that exercises every
reverse RPC outside any invoke.

    page ── tools.invoke(check_start) ──▶ this Executa      returns {task_id} at once
                                            │
                                            └─ keeps running after the invoke returned,
                                               after the window closed, after the user
                                               switched apps:
                                               storage/* files/* web/* sampling/* agent/*
                                               embeddings/* image/* audio/* host/uploadFile

    page ── tools.invoke(check_status) ─▶ this Executa      the task's complete current state

The page only triggers and reads. The task, its progress and its state belong
to this process:

* ``check_start`` registers a task and returns its id. The work runs in an
  asyncio task that outlives the invoke.
* Reverse RPCs issued by that task carry no ``context.invoke_id``. The host
  matches them to no invoke and serves them with the Executa's background
  credentials — the same capabilities an invoke has.
* After every step the whole task state is written to ``scope=app`` storage
  (``bg-check/{task_id}``). Reopening the app reads it back, including rows
  that are still running. After a process restart the unfinished rows are
  reported as ``interrupted``; nothing is re-run automatically.
* Independently of any task, the process writes a heartbeat to storage when it
  starts and every five minutes — work that no user action triggers.

Every check spends a small amount of the user's CU. Video and music are off by
default because they cost considerably more.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

# Fallback for fresh checkouts: locate the in-repo SDK when not pip-installed.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[4] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    PROTOCOL_VERSION_V2,
    AgentSessionClient,
    EmbeddingsClient,
    FilesClient,
    HostUploadClient,
    ImageClient,
    MediaClient,
    SamplingClient,
    StorageClient,
    WebClient,
    WebError,
    bind_invoke,
)
from executa_sdk.web import WEB_ERR_PROVIDER_ERROR, WEB_ERR_TIMEOUT  # noqa: E402

STATE_SCOPE = "app"
KEY_PREFIX = "bg-check/"
INDEX_KEY = KEY_PREFIX + "index"
HEARTBEAT_KEY = KEY_PREFIX + "heartbeat"
PROBE_KEY = KEY_PREFIX + "probe"
PROBE_PATH = KEY_PREFIX + "probe.txt"

HEARTBEAT_INTERVAL_S = 300
MAX_TASKS = 5
TASK_TTL_S = 7 * 24 * 3600
DEFAULT_DELAY_S = 5
MAX_DELAY_S = 120

# ─── Reverse-RPC clients ─────────────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


storage = StorageClient(write_frame=_write_frame)
files = FilesClient(write_frame=_write_frame)
web = WebClient(write_frame=_write_frame)
sampling = SamplingClient(write_frame=_write_frame)
agent = AgentSessionClient(write_frame=_write_frame)
embeddings = EmbeddingsClient(write_frame=_write_frame)
image = ImageClient(write_frame=_write_frame)
media = MediaClient(write_frame=_write_frame)
upload = HostUploadClient(write_frame=_write_frame)

_CLIENTS = (storage, files, web, sampling, agent, embeddings, image, media, upload)

# ─── Checks: one reverse-RPC family each ─────────────────────────────
# Each returns a one-line detail for the table. Failures raise; the runner
# records the error code and message.


async def _check_storage_kv() -> str:
    value = {"nonce": uuid.uuid4().hex}
    await storage.set(PROBE_KEY, value, scope=STATE_SCOPE, ttl_seconds=TASK_TTL_S)
    got = await storage.get(PROBE_KEY, scope=STATE_SCOPE)
    if got.get("value") != value:
        raise RuntimeError("read-back did not return the value just written")
    return "set + get round-trip"


def _put(url: str, headers: dict, payload: bytes) -> str | None:
    req = urllib.request.Request(url, data=payload, method="PUT", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - presigned URL
        if resp.status not in (200, 201):
            raise RuntimeError(f"object upload PUT failed: HTTP {resp.status}")
        return resp.headers.get("ETag")


async def _check_files() -> str:
    payload = f"background check {_now()}\n".encode()
    begin = await files.upload_begin(
        path=PROBE_PATH,
        size_bytes=len(payload),
        content_type="text/plain; charset=utf-8",
        scope=STATE_SCOPE,
    )
    etag = await asyncio.to_thread(_put, begin["put_url"], begin.get("headers") or {}, payload)
    await files.upload_complete(
        path=PROBE_PATH, etag=etag, size_bytes=len(payload), scope=STATE_SCOPE
    )
    link = await files.download_url(path=PROBE_PATH, scope=STATE_SCOPE)
    if not link.get("url"):
        raise RuntimeError("download_url returned no url")
    return f"uploaded {len(payload)} bytes, download link issued"


async def _check_web_search() -> str:
    res = await web.search(query="Anna AI assistant", max_results=3)
    return f"{len(res.get('results') or [])} results"


async def _check_web_fetch() -> str:
    res = await web.fetch(urls=["https://example.com"], max_chars=2000)
    page = (res.get("pages") or [{}])[0]
    if not page.get("ok"):
        raise RuntimeError(f"page not fetched: {page.get('error')}")
    return f"fetched {len(page.get('content') or '')} chars"


async def _image_urls() -> list[str]:
    res = await web.image_search(query="mountain lake", max_results=3)
    return [r["image_url"] for r in res.get("results") or [] if r.get("image_url")]


async def _check_web_image_search() -> str:
    return f"{len(await _image_urls())} images"


# A search result points at a third-party host that may refuse the download or
# time out, which says nothing about the reverse RPC. Those failures move on to
# the next result. Anything else (not granted, quota, bad request) is about
# this caller and is reported at once.
_SOURCE_FAILURES = (WEB_ERR_PROVIDER_ERROR, WEB_ERR_TIMEOUT)


async def _check_web_image_fetch() -> str:
    failure: Exception = RuntimeError("image search returned nothing to fetch")
    for skipped, url in enumerate(await _image_urls()):
        try:
            res = await web.image_fetch(url=url)
        except WebError as e:
            if e.code not in _SOURCE_FAILURES:
                raise
            failure = e
            continue
        detail = f"stored {res.get('bytes_size')} bytes ({res.get('mime_type')})"
        return f"{detail}; skipped {skipped} unreachable source(s)" if skipped else detail
    raise failure


async def _check_sampling() -> str:
    res = await sampling.create_message(
        messages=[
            {"role": "user", "content": {"type": "text", "text": "Reply with the single word: pong"}}
        ],
        max_tokens=16,
    )
    text = ((res.get("content") or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError("empty completion")
    return f"model replied {text[:40]!r}"


async def _check_agent_session() -> str:
    session = await agent.create(label="Background check")
    try:
        text, finished = "", False
        async for frame in session.run("Reply with the single word: pong"):
            if frame.get("event") == "delta":
                text += frame.get("text") or ""
            elif frame.get("event") == "final":
                text, finished = frame.get("text") or text, True
        if not finished:
            raise RuntimeError("run ended without a final frame")
        if not text.strip():
            raise RuntimeError("run finished with an empty reply")
        return f"agent replied {text.strip()[:40]!r}"
    finally:
        try:
            await session.delete()
        except Exception:  # noqa: BLE001 - cleanup only
            pass


async def _check_embeddings() -> str:
    res = await embeddings.create(input="background check")
    vector = ((res.get("data") or [{}])[0]).get("embedding") or []
    if not vector:
        raise RuntimeError("no embedding returned")
    return f"{len(vector)}-dimensional vector"


async def _check_image_generate() -> str:
    res = await image.generate(prompt="a small flat blue square icon", n=1)
    if not res.get("images"):
        raise RuntimeError("no image returned")
    return f"{len(res['images'])} image"


async def _check_audio_speak() -> str:
    res = await media.speak(text="Background check.")
    if not (res.get("url") or res.get("audioBase64")):
        raise RuntimeError("no audio returned")
    return f"{res.get('charCount')} chars spoken"


async def _check_host_upload() -> str:
    res = await upload.upload_inline(
        filename="background-check.txt",
        mime_type="text/plain",
        content=f"background check {_now()}\n".encode(),
    )
    if not res.get("download_url"):
        raise RuntimeError("no download_url returned")
    return f"uploaded {res.get('size_bytes')} bytes"


async def _check_video() -> str:
    job = await media.generate_await(prompt="a calm ocean wave", duration_sec=5)
    return f"job {job.get('jobId') or job.get('job_id')} finished"


async def _check_music() -> str:
    job = await media.music_generate_await(
        prompt="a short calm piano phrase", instrumental=True, duration_sec=10
    )
    return f"job {job.get('jobId') or job.get('job_id')} finished"


@dataclass(frozen=True)
class Check:
    label: str
    method: str
    run: Callable[[], Awaitable[str]]
    default: bool = True
    timeout_s: float = 180.0


CHECKS: dict[str, Check] = {
    "storage_kv": Check("Storage KV (scope=app)", "storage/set + storage/get", _check_storage_kv),
    "files": Check("Object storage (scope=app)", "files/upload_begin … download_url", _check_files),
    "web_search": Check("Web search", "web/search", _check_web_search),
    "web_fetch": Check("Web fetch", "web/fetch", _check_web_fetch),
    "web_image_search": Check("Image search", "web/image_search", _check_web_image_search),
    "web_image_fetch": Check(
        "Image fetch", "web/image_search + web/image_fetch", _check_web_image_fetch
    ),
    "sampling": Check("LLM sampling", "sampling/createMessage", _check_sampling),
    "agent_session": Check(
        "Agent session", "agent/session.create + agent/session.run", _check_agent_session, timeout_s=330.0
    ),
    "embeddings": Check("Embeddings", "embeddings/create", _check_embeddings),
    "image_generate": Check("Image generation", "image/generate", _check_image_generate),
    "audio_speak": Check("Text to speech", "audio/speak", _check_audio_speak),
    "host_upload": Check("Host upload", "host/uploadFile", _check_host_upload),
    "video_generate": Check(
        "Video generation (costly)", "video/generate", _check_video, default=False, timeout_s=1500.0
    ),
    "music_generate": Check(
        "Music generation (costly)", "music/generate", _check_music, default=False, timeout_s=900.0
    ),
}

# ─── Task state ──────────────────────────────────────────────────────
# Memory is the working copy; storage is what survives the process.

_tasks: dict[str, dict] = {}
_runners: dict[str, asyncio.Task] = {}
_heartbeat: dict[str, Any] = {
    "process_started_at": None,
    "interval_s": HEARTBEAT_INTERVAL_S,
    "count": 0,
    "last_ok_at": None,
    "last_error": None,
}

_UNFINISHED = ("pending", "running")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error_info(e: BaseException) -> dict:
    return {"code": getattr(e, "code", None), "message": getattr(e, "message", None) or str(e)}


def _new_task(item_ids: list[str], delay_s: float) -> dict:
    return {
        # Sortable by creation time.
        "task_id": f"{int(time.time())}-{uuid.uuid4().hex[:6]}",
        "status": "pending",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "delay_s": delay_s,
        "persist_error": None,
        "items": [
            {
                "id": item_id,
                "label": CHECKS[item_id].label,
                "method": CHECKS[item_id].method,
                "status": "pending",
                "elapsed_ms": None,
                "detail": None,
                "error": None,
            }
            for item_id in item_ids
        ],
    }


def _close_unfinished(task: dict, status: str) -> None:
    for item in task["items"]:
        if item["status"] in _UNFINISHED:
            item["status"] = status
    task["status"] = status
    task["finished_at"] = _now()


async def _persist(task: dict) -> None:
    """Write the whole task to storage. A failed write never stops the task:
    the error is kept on the task so the page can show why state is not durable."""
    try:
        await storage.set(
            KEY_PREFIX + task["task_id"], task, scope=STATE_SCOPE, ttl_seconds=TASK_TTL_S
        )
        task["persist_error"] = None
    except Exception as e:  # noqa: BLE001
        task["persist_error"] = _error_info(e)


async def _get_task(task_id: str) -> dict | None:
    task = _tasks.get(task_id)
    if task is not None:
        return task
    got = await storage.get(KEY_PREFIX + task_id, scope=STATE_SCOPE)
    task = got.get("value") if got.get("exists") else None
    if not isinstance(task, dict):
        return None
    if task.get("status") in _UNFINISHED:
        # Stored as unfinished but unknown to this process: a previous
        # process was running it when it stopped.
        _close_unfinished(task, "interrupted")
        await _persist(task)
    _tasks[task_id] = task
    return task


async def _read_index() -> list[str]:
    got = await storage.get(INDEX_KEY, scope=STATE_SCOPE)
    ids = (got.get("value") or {}).get("task_ids") if got.get("exists") else None
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


_index_lock = asyncio.Lock()


async def _index_add(task_id: str) -> None:
    async with _index_lock:
        ids = [task_id, *(i for i in await _read_index() if i != task_id)][:MAX_TASKS]
        await storage.set(INDEX_KEY, {"task_ids": ids}, scope=STATE_SCOPE)
    for stale in set(_tasks) - set(ids) - set(_runners):
        _tasks.pop(stale, None)


async def _run_task(task: dict) -> None:
    # This asyncio task was created inside the check_start handler and
    # inherited that invoke's binding. Detach from it: the invoke is about to
    # return, and everything below runs on the Executa's background identity.
    with bind_invoke(None):
        try:
            await asyncio.sleep(task["delay_s"])
            task["status"] = "running"
            task["started_at"] = _now()
            await _persist(task)
            for item in task["items"]:
                check = CHECKS[item["id"]]
                item["status"] = "running"
                started = time.perf_counter()
                try:
                    item["detail"] = await asyncio.wait_for(check.run(), check.timeout_s)
                    item["status"] = "ok"
                except asyncio.TimeoutError:
                    item["status"] = "failed"
                    item["error"] = {"code": None, "message": f"timed out after {check.timeout_s:.0f}s"}
                except Exception as e:  # noqa: BLE001 - every failure is a table row
                    item["status"] = "failed"
                    item["error"] = _error_info(e)
                item["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                await _persist(task)
            task["status"] = "done"
            task["finished_at"] = _now()
        except asyncio.CancelledError:
            _close_unfinished(task, "cancelled")
        finally:
            _runners.pop(task["task_id"], None)
            await _persist(task)


async def _heartbeat_loop() -> None:
    """Work nobody triggers: runs from process start, outside any invoke."""
    _heartbeat["process_started_at"] = _now()
    while True:
        _heartbeat["count"] += 1
        try:
            await storage.set(
                HEARTBEAT_KEY,
                {"at": _now(), "count": _heartbeat["count"]},
                scope=STATE_SCOPE,
            )
            _heartbeat["last_ok_at"] = _now()
            _heartbeat["last_error"] = None
        except Exception as e:  # noqa: BLE001
            _heartbeat["last_error"] = {**_error_info(e), "at": _now()}
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


# ─── Tools ───────────────────────────────────────────────────────────


async def check_start(items: list[str] | None = None, delay_s: float = DEFAULT_DELAY_S) -> dict:
    if items is None:
        item_ids = [i for i, c in CHECKS.items() if c.default]
    else:
        unknown = [i for i in items if i not in CHECKS]
        if unknown or not items:
            raise ValueError(f"items must be a non-empty subset of {list(CHECKS)}; unknown: {unknown}")
        item_ids = [i for i in CHECKS if i in items]
    delay = min(MAX_DELAY_S, max(0.0, float(delay_s)))

    task = _new_task(item_ids, delay)
    _tasks[task["task_id"]] = task
    await _persist(task)
    try:
        await _index_add(task["task_id"])
    except Exception as e:  # noqa: BLE001
        task["persist_error"] = _error_info(e)
    _runners[task["task_id"]] = asyncio.get_running_loop().create_task(_run_task(task))
    return {"task_id": task["task_id"], "task": task}


async def check_status(task_id: str) -> dict:
    task = await _get_task(str(task_id))
    return {"found": task is not None, "task_id": task_id, "task": task}


async def check_list() -> dict:
    out: dict[str, Any] = {
        "checks": [
            {"id": i, "label": c.label, "method": c.method, "default": c.default}
            for i, c in CHECKS.items()
        ],
        "heartbeat": _heartbeat,
        "storage_error": None,
    }
    try:
        ids = await _read_index()
        tasks = [await _get_task(i) for i in ids]
    except Exception as e:  # noqa: BLE001
        # Storage unreachable: answer with what this process still knows.
        out["storage_error"] = _error_info(e)
        tasks = sorted(_tasks.values(), key=lambda t: t["task_id"], reverse=True)
    out["tasks"] = [t for t in tasks if t is not None]
    return out


async def check_cancel(task_id: str) -> dict:
    runner = _runners.get(str(task_id))
    if runner is None:
        return {"cancelled": False, "task": _tasks.get(str(task_id))}
    runner.cancel()
    await asyncio.wait([runner], timeout=10)
    return {"cancelled": True, "task": _tasks.get(str(task_id))}


TOOLS: dict[str, Callable[..., Awaitable[dict]]] = {
    "check_start": check_start,
    "check_status": check_status,
    "check_list": check_list,
    "check_cancel": check_cancel,
}

MANIFEST = {
    "display_name": "Background Check",
    "version": "0.1.0",
    "description": (
        "Runs a capability health check as a backend-owned task: every reverse "
        "RPC is issued outside any invoke, and the task keeps its own state."
    ),
    "author": "Anna Developer",
    "tools": [
        {
            "name": "check_start",
            "description": (
                "Start a health check and return its task_id immediately. The "
                "check itself runs after this call has returned."
            ),
            "parameters": [
                {
                    "name": "items",
                    "type": "array",
                    "description": f"Checks to run; defaults to all but the costly ones. One of: {', '.join(CHECKS)}.",
                    "required": False,
                },
                {
                    "name": "delay_s",
                    "type": "number",
                    "description": f"Seconds to wait before the first check (0–{MAX_DELAY_S}).",
                    "required": False,
                    "default": DEFAULT_DELAY_S,
                },
            ],
        },
        {
            "name": "check_status",
            "description": "The complete current state of one task, including rows still running.",
            "parameters": [
                {"name": "task_id", "type": "string", "description": "Id returned by check_start.", "required": True}
            ],
        },
        {
            "name": "check_list",
            "description": "Running and recent tasks with their complete state, the heartbeat, and the check catalog.",
            "parameters": [],
        },
        {
            "name": "check_cancel",
            "description": "Stop a running task.",
            "parameters": [
                {"name": "task_id", "type": "string", "description": "Id returned by check_start.", "required": True}
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}

# ─── JSON-RPC dispatch ───────────────────────────────────────────────


def _make_response(req_id, *, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()
_heartbeat_started = False


def _handle_initialize(req_id, params: dict) -> dict:
    global _heartbeat_started
    proto = (params or {}).get("protocolVersion") or "1.1"
    if proto != PROTOCOL_VERSION_V2:
        for client in _CLIENTS:
            client.disable(f"reverse RPC requires Executa protocol 2.0 (host offered {proto!r})")
    elif not _heartbeat_started:
        _heartbeat_started = True
        asyncio.run_coroutine_threadsafe(_heartbeat_loop(), _loop)
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": {"sampling": {}} if proto == PROTOCOL_VERSION_V2 else {},
            "capabilities": {},
        },
    )


async def _invoke(req_id, params: dict) -> None:
    tool = TOOLS.get(params.get("tool"))
    if tool is None:
        _write_frame(
            _make_response(req_id, error={"code": -32601, "message": f"Unknown tool: {params.get('tool')}"})
        )
        return
    try:
        # Reverse RPCs issued while answering this invoke belong to it.
        with bind_invoke(params):
            data = await tool(**(params.get("arguments") or {}))
    except (TypeError, ValueError) as e:
        _write_frame(_make_response(req_id, error={"code": -32602, "message": f"Invalid params: {e}"}))
        return
    except Exception as e:  # noqa: BLE001
        info = _error_info(e)
        _write_frame(
            _make_response(
                req_id, error={"code": info["code"] or -32603, "message": info["message"]}
            )
        )
        return
    _write_frame(
        _make_response(req_id, result={"success": True, "tool": params.get("tool"), "data": data})
    )


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reply to one of our reverse RPCs.
    if "method" not in msg:
        if not any(client.dispatch_response(msg) for client in _CLIENTS):
            print(f"⚠️  unmatched response id={msg.get('id')!r}", file=sys.stderr)
        return
    # Live frame of an in-flight agent/session.run.
    if agent.dispatch_notification(msg):
        return

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "invoke":
        # Answered from the asyncio loop, so this thread goes straight back to
        # stdin and replies to background reverse RPCs are never held up.
        asyncio.run_coroutine_threadsafe(_invoke(req_id, params), _loop)
        return

    if method == "initialize":
        resp = _handle_initialize(req_id, params)
    elif method == "describe":
        resp = _make_response(req_id, result=MANIFEST)
    elif method == "health":
        resp = _make_response(req_id, result={"status": "healthy", "version": MANIFEST["version"]})
    elif method == "shutdown":
        resp = _make_response(req_id, result={"ok": True})
    else:
        resp = _make_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    if req_id is not None:
        _write_frame(resp)


def main() -> None:
    print("🔌 background-check plugin started", file=sys.stderr)
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if line:
                _handle_message(line)
    finally:
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
