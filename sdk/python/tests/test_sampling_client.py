"""Tests for executa_sdk.sampling.SamplingClient.create_message param forwarding."""

from __future__ import annotations

import asyncio

import pytest

from executa_sdk.sampling import METHOD_SAMPLING_CREATE_MESSAGE, SamplingClient

_MESSAGES = [{"role": "user", "content": {"type": "text", "text": "hi"}}]
_RESULT = {
    "role": "assistant",
    "content": {"type": "text", "text": "ok"},
    "model": "m",
    "stopReason": "endTurn",
    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
}


async def _roundtrip(**kwargs) -> dict:
    """Run create_message with a fake host that echoes a fixed result; return the wire params."""
    frames: list[dict] = []
    client = SamplingClient(write_frame=frames.append)

    async def _host_reply():
        while not frames:
            await asyncio.sleep(0)
        env = frames[0]
        client.dispatch_response({"jsonrpc": "2.0", "id": env["id"], "result": _RESULT})

    reply_task = asyncio.create_task(_host_reply())
    result = await client.create_message(messages=_MESSAGES, max_tokens=16, **kwargs)
    await reply_task
    assert result == _RESULT
    assert frames[0]["method"] == METHOD_SAMPLING_CREATE_MESSAGE
    return frames[0]["params"]


@pytest.mark.asyncio
async def test_thinking_omitted_when_none():
    params = await _roundtrip()
    assert "thinking" not in params
    assert "responseFormat" not in params


@pytest.mark.asyncio
async def test_thinking_false_is_forwarded_verbatim():
    params = await _roundtrip(thinking=False)
    assert params["thinking"] is False


@pytest.mark.asyncio
async def test_thinking_object_is_forwarded_verbatim():
    spec = {"enabled": True, "effort": "low", "budgetTokens": 1024, "includeReasoning": True}
    params = await _roundtrip(thinking=spec)
    assert params["thinking"] == spec


@pytest.mark.asyncio
async def test_response_format_is_forwarded_verbatim():
    params = await _roundtrip(response_format={"type": "json_object"}, on_unsupported="text")
    assert params["responseFormat"] == {"type": "json_object"}
    assert params["onUnsupported"] == "text"
