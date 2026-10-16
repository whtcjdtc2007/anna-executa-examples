"""Smoke tests for ``MediaClient.transcribe`` (``audio/transcribe`` reverse RPC).

Fixture ``fixtures/silence_2s_16k.wav`` is a 2 s / 16 kHz / mono PCM16 wav
(≈64 KB) — exactly the inline shape a push-to-talk plugin would send.
Run with: ``pytest -q`` (from anna-executa-examples/sdk/python).
"""

from __future__ import annotations

import asyncio
import base64
import pathlib

import pytest

from executa_sdk.media import (
    AUDIO_ERR_INVOKE_BUDGET_EXCEEDED,
    AUDIO_ERR_NOT_GRANTED,
    METHOD_AUDIO_TRANSCRIBE,
    VIDEO_ERR_INVALID_REQUEST,
    MediaClient,
    MediaError,
)

_WAV = pathlib.Path(__file__).parent / "fixtures" / "silence_2s_16k.wav"


class _FakeHost:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def write(self, msg: dict) -> None:
        self.frames.append(msg)


@pytest.mark.asyncio
async def test_transcribe_inline_wav_envelope_and_result():
    host = _FakeHost()
    client = MediaClient(write_frame=host.write)
    audio = _WAV.read_bytes()
    task = asyncio.create_task(client.transcribe(audio_bytes=audio, format="wav", language="zh"))
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_AUDIO_TRANSCRIBE
    assert env["params"]["format"] == "wav"
    assert env["params"]["language"] == "zh"
    assert base64.b64decode(env["params"]["audioBase64"]) == audio
    assert "audioUrl" not in env["params"]
    client.dispatch_response(
        {
            "jsonrpc": "2.0",
            "id": env["id"],
            "result": {"text": "我现在该出什么", "durationSec": 2.0, "durationExact": True, "billedCostCU": 1},
        }
    )
    out = await task
    assert out["text"] == "我现在该出什么" and out["durationSec"] == 2.0


@pytest.mark.asyncio
async def test_transcribe_url_variant():
    host = _FakeHost()
    client = MediaClient(write_frame=host.write)
    task = asyncio.create_task(client.transcribe(audio_url="https://cdn/x.mp3"))
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["params"] == {"audioUrl": "https://cdn/x.mp3"} or set(env["params"]) >= {"audioUrl"}
    assert "format" not in env["params"]
    client.dispatch_response({"jsonrpc": "2.0", "id": env["id"], "result": {"text": "x"}})
    assert (await task)["text"] == "x"


@pytest.mark.asyncio
async def test_transcribe_requires_exactly_one_source():
    client = MediaClient(write_frame=lambda m: None)
    with pytest.raises(MediaError) as e:
        await client.transcribe()
    assert e.value.code == VIDEO_ERR_INVALID_REQUEST
    with pytest.raises(MediaError):
        await client.transcribe(audio_bytes=b"x", audio_url="https://a")


@pytest.mark.asyncio
async def test_transcribe_disabled_maps_to_audio_not_granted():
    client = MediaClient(write_frame=lambda m: None)
    client.disable("llm.audio.transcribe not negotiated")
    with pytest.raises(MediaError) as e:
        await client.transcribe(audio_bytes=b"x", format="wav")
    assert e.value.code == AUDIO_ERR_NOT_GRANTED


@pytest.mark.asyncio
async def test_transcribe_budget_error_surfaces():
    host = _FakeHost()
    client = MediaClient(write_frame=host.write)
    task = asyncio.create_task(client.transcribe(audio_bytes=b"x", format="wav"))
    await asyncio.sleep(0)
    client.dispatch_response(
        {
            "jsonrpc": "2.0",
            "id": host.frames[0]["id"],
            "error": {"code": AUDIO_ERR_INVOKE_BUDGET_EXCEEDED, "message": "budget", "data": {"cap": 600}},
        }
    )
    with pytest.raises(MediaError) as e:
        await task
    assert e.value.code == AUDIO_ERR_INVOKE_BUDGET_EXCEEDED and e.value.data == {"cap": 600}
