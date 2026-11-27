"""Tests for executa_sdk.agent — live frames on ``run``, ``attach`` and ``catalog``."""

from __future__ import annotations

import asyncio

import pytest

from executa_sdk.agent import (
    METHOD_AGENT_SESSION_CANCEL,
    METHOD_AGENT_SESSION_CATALOG,
    METHOD_AGENT_SESSION_FRAME,
    METHOD_AGENT_SESSION_REFRESH,
    METHOD_AGENT_SESSION_RUN,
    AgentError,
    AgentSession,
    AgentSessionClient,
)

FRAMES = [{"event": "delta", "text": t, "seq": i} for i, t in enumerate("abcd")]


class _Host:
    """A fake host on the other end of the plugin's stdio."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.client = AgentSessionClient(write_frame=self.sent.append)
        self.session = AgentSession(
            uuid="aps_x",
            expires_in=600,
            kind="agent",
            agent_submode="auto",
            fixed_client_id=None,
            granted_tools=[],
        )
        self.session._client = self.client

    async def request(self, method: str) -> dict:
        """Wait for the plugin's next request of ``method``."""
        while True:
            for env in self.sent:
                if env["method"] == method:
                    return env
            await asyncio.sleep(0)

    def push(self, run: dict, *seqs: int) -> None:
        for seq in seqs:
            assert self.client.dispatch_notification(
                {
                    "jsonrpc": "2.0",
                    "method": METHOD_AGENT_SESSION_FRAME,
                    "params": {
                        "run_id": run["params"]["run_id"],
                        "seq": seq,
                        "frame": FRAMES[seq],
                    },
                }
            )

    def respond(self, env: dict, *, result: dict | None = None, error: dict | None = None):
        msg = {"jsonrpc": "2.0", "id": env["id"]}
        msg.update({"error": error} if error else {"result": result or {}})
        assert self.client.dispatch_response(msg)

    def finish(self, run: dict) -> None:
        self.respond(run, result={"run_id": run["params"]["run_id"], "frames": FRAMES})


async def _collect(host: _Host, seen: list[dict]) -> None:
    async for frame in host.session.run("hi"):
        seen.append(frame)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_frames_are_yielded_before_the_response():
    host, seen = _Host(), []
    consumer = asyncio.create_task(_collect(host, seen))
    run = await host.request(METHOD_AGENT_SESSION_RUN)
    assert run["params"]["stream"] is True
    assert run["params"]["run_id"].startswith("run_")

    host.push(run, 0, 1)
    await _settle()
    assert seen == FRAMES[:2] and not consumer.done()

    host.finish(run)
    await consumer
    assert seen == FRAMES
    assert host.client._streams == {}


@pytest.mark.asyncio
async def test_host_that_never_pushes_delivers_everything_with_the_response():
    host, seen = _Host(), []
    consumer = asyncio.create_task(_collect(host, seen))
    host.finish(await host.request(METHOD_AGENT_SESSION_RUN))
    await consumer
    assert seen == FRAMES


@pytest.mark.asyncio
async def test_out_of_order_frames_are_yielded_by_seq_without_duplicates():
    host, seen = _Host(), []
    consumer = asyncio.create_task(_collect(host, seen))
    run = await host.request(METHOD_AGENT_SESSION_RUN)

    host.push(run, 1)  # a gap: nothing may be yielded yet
    await _settle()
    assert seen == []
    host.push(run, 0, 0, 3)  # fills the gap; a repeat and a frame past a new gap
    await _settle()
    assert seen == FRAMES[:2]

    host.finish(run)
    await consumer
    assert seen == FRAMES


@pytest.mark.asyncio
async def test_leaving_the_loop_early_cancels_the_upstream_run():
    host = _Host()

    async def first_frame() -> dict:
        async for frame in host.session.run("hi", run_id="run_mine"):
            return frame

    consumer = asyncio.create_task(first_frame())
    run = await host.request(METHOD_AGENT_SESSION_RUN)
    host.push(run, 0)

    cancel = await host.request(METHOD_AGENT_SESSION_CANCEL)
    assert cancel["params"]["run_id"] == "run_mine"
    assert cancel["params"]["app_session_uuid"] == "aps_x"
    host.respond(cancel)
    assert await consumer == FRAMES[0]
    assert host.client._streams == {}


@pytest.mark.asyncio
async def test_error_after_partial_frames_raises_after_yielding_them():
    host, seen = _Host(), []
    consumer = asyncio.create_task(_collect(host, seen))
    run = await host.request(METHOD_AGENT_SESSION_RUN)
    host.push(run, 0)
    await _settle()
    host.respond(run, error={"code": -32046, "message": "upstream failed"})

    with pytest.raises(AgentError) as exc:
        await consumer
    assert exc.value.code == -32046
    assert seen == FRAMES[:1]
    assert all(env["method"] != METHOD_AGENT_SESSION_CANCEL for env in host.sent)


@pytest.mark.asyncio
async def test_attach_refreshes_first_and_catalog_is_routed():
    host = _Host()

    attaching = asyncio.create_task(host.client.attach("aps_other"))
    refresh = await host.request(METHOD_AGENT_SESSION_REFRESH)
    assert refresh["params"]["app_session_uuid"] == "aps_other"
    host.respond(
        refresh,
        result={"app_session_uuid": "aps_other", "expires_in": 300, "kind": "agent"},
    )
    session = await attaching
    assert (session.uuid, session.expires_in) == ("aps_other", 300)
    assert session._client is host.client

    reading = asyncio.create_task(host.client.catalog())
    host.respond(
        await host.request(METHOD_AGENT_SESSION_CATALOG),
        result={"platform_tools": []},
    )
    assert await reading == {"platform_tools": []}
