"""``ImageClient`` async-job surface (``image/{generate_async, edit_async, get_job,
cancel_job, resign}``; platform-media-generation-image-jobs.md §11.5).

Pure-transport tests: a fake host records outgoing frames and the test feeds
responses through ``dispatch_response``. ``generate_await`` is driven with a
patched ``asyncio.sleep`` so the 5 s poll cadence does not slow the suite.
Run with: ``pytest -q`` (from anna-executa-examples/sdk/python).
"""

from __future__ import annotations

import asyncio

import pytest

import executa_sdk.image as image_mod
from executa_sdk.image import (
    IMAGE_ERR_CONTENT_REJECTED,
    IMAGE_ERR_JOB_NOT_FOUND,
    METHOD_IMAGE_CANCEL_JOB,
    METHOD_IMAGE_EDIT_ASYNC,
    METHOD_IMAGE_GENERATE_ASYNC,
    METHOD_IMAGE_GET_JOB,
    METHOD_IMAGE_RESIGN,
    ImageClient,
    ImageError,
    ImageJobTimeout,
)


class _FakeHost:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def write(self, msg: dict) -> None:
        self.frames.append(msg)


def _reply(client: ImageClient, env: dict, result: dict | None = None, error: dict | None = None):
    msg = {"jsonrpc": "2.0", "id": env["id"]}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result or {}
    client.dispatch_response(msg)


@pytest.fixture
def fast_sleep(monkeypatch):
    real_sleep = asyncio.sleep

    async def _sleep(_s):
        await real_sleep(0)

    monkeypatch.setattr(image_mod.asyncio, "sleep", _sleep)


@pytest.mark.asyncio
async def test_generate_async_envelope():
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(
        client.generate_async(
            "a cat",
            n=2,
            size="2048x1152",
            model="fal-ai/nano-banana-2",
            params={"resolution": "2K"},
            reference_image_urls=["https://r/a.png"],
            client_tag="t",
            idempotency_key="inv:1",
        )
    )
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_IMAGE_GENERATE_ASYNC
    p = env["params"]
    assert p["prompt"] == "a cat" and p["n"] == 2 and p["size"] == "2048x1152"
    assert p["model"] == "fal-ai/nano-banana-2" and p["params"] == {"resolution": "2K"}
    assert p["fit"] == "nearest" and p["reference_image_urls"] == ["https://r/a.png"]
    assert p["clientTag"] == "t" and p["idempotencyKey"] == "inv:1"
    _reply(client, env, {"jobId": "ijob_x", "kind": "image", "state": "queued", "estimatedCostCU": 48})
    out = await task
    assert out["jobId"] == "ijob_x" and out["state"] == "queued"


@pytest.mark.asyncio
async def test_edit_async_get_cancel_resign_envelopes():
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    t1 = asyncio.create_task(client.edit_async("https://in.png", "replace sky", mask_url="https://m.png", params={"quality": "high"}))
    await asyncio.sleep(0)
    e1 = host.frames[0]
    assert e1["method"] == METHOD_IMAGE_EDIT_ASYNC
    assert e1["params"]["image_url"] == "https://in.png" and e1["params"]["mask_url"] == "https://m.png"
    assert e1["params"]["params"] == {"quality": "high"} and e1["params"]["n"] == 1
    _reply(client, e1, {"jobId": "ijob_e", "state": "queued"})
    assert (await t1)["jobId"] == "ijob_e"

    t2 = asyncio.create_task(client.get_job("ijob_e"))
    t3 = asyncio.create_task(client.cancel_job("ijob_e"))
    t4 = asyncio.create_task(client.resign(["k1", "k2"]))
    await asyncio.sleep(0)
    e2, e3, e4 = host.frames[1:4]
    assert (e2["method"], e2["params"]["jobId"]) == (METHOD_IMAGE_GET_JOB, "ijob_e")
    assert (e3["method"], e3["params"]["jobId"]) == (METHOD_IMAGE_CANCEL_JOB, "ijob_e")
    assert (e4["method"], e4["params"]["r2Keys"]) == (METHOD_IMAGE_RESIGN, ["k1", "k2"])
    _reply(client, e2, {"jobId": "ijob_e", "state": "running"})
    _reply(client, e3, {"jobId": "ijob_e", "state": "cancelled", "refunded": True})
    _reply(client, e4, {"urls": [{"r2Key": "k1", "url": "u1", "expiresIn": 1800}, {"r2Key": "k2", "url": "u2", "expiresIn": 1800}]})
    assert (await t2)["state"] == "running"
    assert (await t3)["refunded"] is True
    assert [u["r2Key"] for u in (await t4)["urls"]] == ["k1", "k2"]


@pytest.mark.asyncio
async def test_error_envelope_maps_to_image_error():
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(client.get_job("mjob_other"))
    await asyncio.sleep(0)
    _reply(client, host.frames[0], error={"code": IMAGE_ERR_JOB_NOT_FOUND, "message": "job not found", "data": {}})
    with pytest.raises(ImageError) as ei:
        await task
    assert ei.value.code == IMAGE_ERR_JOB_NOT_FOUND


# ---------- generate_await: succeeded / failed / timeout ----------


async def _drive(host: _FakeHost, client: ImageClient, task: asyncio.Task, views: list[dict]):
    """Answer the submit frame, then each new get_job frame with ``views`` in order."""
    answered = 0

    async def _next_frame() -> dict:
        for _ in range(500):
            if len(host.frames) > answered:
                return host.frames[answered]
            await asyncio.sleep(0)
        raise AssertionError(f"no new frame after {answered} answered")

    env = await _next_frame()
    answered += 1
    _reply(client, env, {"jobId": "ijob_w", "kind": "image", "state": "queued"})
    for view in views:
        if task.done():
            break
        env = await _next_frame()
        answered += 1
        assert env["method"] == METHOD_IMAGE_GET_JOB and env["params"]["jobId"] == "ijob_w"
        _reply(client, env, view)


@pytest.mark.asyncio
async def test_generate_await_succeeds_with_progress(fast_sleep):
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    progress: list[dict] = []
    task = asyncio.create_task(
        client.generate_await("a cat", n=2, on_progress=progress.append, poll_interval_s=0.01, await_timeout_s=10)
    )
    await _drive(
        host,
        client,
        task,
        [
            {"jobId": "ijob_w", "state": "queued", "progress": {"phase": "IN_QUEUE", "queuePosition": 2}},
            {"jobId": "ijob_w", "state": "running", "progress": {"phase": "IN_PROGRESS"}},
            {"jobId": "ijob_w", "state": "succeeded", "result": {"images": [{"url": "u", "r2Key": "k"}]}, "billedCostCU": 48},
        ],
    )
    out = await task
    assert out["state"] == "succeeded" and out["result"]["images"][0]["r2Key"] == "k"
    assert [p["phase"] for p in progress] == ["IN_QUEUE", "IN_PROGRESS"]
    assert host.frames[0]["method"] == METHOD_IMAGE_GENERATE_ASYNC
    assert host.frames[0]["params"]["n"] == 2


@pytest.mark.asyncio
async def test_generate_await_failed_maps_terminal_error(fast_sleep):
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(client.generate_await("x", poll_interval_s=0.01, await_timeout_s=10))
    await _drive(
        host,
        client,
        task,
        [{"jobId": "ijob_w", "state": "failed", "error": {"code": "IMAGE_CONTENT_REJECTED", "message": "nsfw"}}],
    )
    with pytest.raises(ImageError) as ei:
        await task
    assert ei.value.code == IMAGE_ERR_CONTENT_REJECTED
    assert ei.value.data["jobId"] == "ijob_w" and ei.value.data["state"] == "failed"


@pytest.mark.asyncio
async def test_generate_await_timeout_carries_job_id(fast_sleep):
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(client.generate_await("x", poll_interval_s=1.0, await_timeout_s=2.0))
    await _drive(
        host,
        client,
        task,
        [{"jobId": "ijob_w", "state": "running"}, {"jobId": "ijob_w", "state": "running"}],
    )
    with pytest.raises(ImageJobTimeout) as ei:
        await task
    assert ei.value.job_id == "ijob_w" and ei.value.waited_s == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_generate_await_idempotent_replay_terminal_returns_immediately():
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(client.generate_await("x", idempotency_key="k"))
    await asyncio.sleep(0)
    _reply(client, host.frames[0], {"jobId": "ijob_r", "state": "succeeded", "idempotentReplay": True, "result": {"images": []}})
    out = await task
    assert out["idempotentReplay"] is True and len(host.frames) == 1


@pytest.mark.asyncio
async def test_edit_await_submits_edit_async(fast_sleep):
    host = _FakeHost()
    client = ImageClient(write_frame=host.write)
    task = asyncio.create_task(client.edit_await("https://in.png", "p", mask_url="https://m.png", poll_interval_s=0.01, await_timeout_s=10))
    await _drive(host, client, task, [{"jobId": "ijob_w", "state": "succeeded", "result": {"images": []}}])
    await task
    assert host.frames[0]["method"] == METHOD_IMAGE_EDIT_ASYNC
    assert host.frames[0]["params"]["mask_url"] == "https://m.png"
