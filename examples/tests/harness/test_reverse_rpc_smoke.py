"""Every reverse RPC the demo Executas issue is served by ``anna-app dev``.

The demos' "Reverse RPC" switches are only as good as the local runtime
underneath them: forum #598 was a demo button that had answered ``-32601``
for months because nothing ever ran it. Each test invokes a real demo
plugin on the real runtime (see ``harness.py``) and checks which
``host.<ns>.<method>`` the runtime forwarded.

    uv run --no-project --prerelease allow \\
        --with-requirements tests/harness/requirements.txt \\
        pytest tests/harness -v            # from examples/
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from harness import TOOL_ID, Harness

WEB = "anna-app-web-search-demo/executas/web-search-via-executa-python/web_search_via_executa_plugin.py"
MEDIA = "anna-app-media-studio/executas/clip-narrator/clip_narrator.py"
FILES = "anna-app-aps-files-demo/executas/files-via-executa-python/files_via_executa_plugin.py"
CREDENTIALS = (
    "anna-app-credentials-demo/executas/credentials-via-executa-python/"
    "credentials_via_executa_plugin.py"
)


@asynccontextmanager
async def running(plugin: str, responses: dict | None = None):
    harness = Harness(plugin, responses)
    try:
        yield harness
    finally:
        await harness.close()


def test_web_search_demo_reaches_all_four_web_methods():
    async def scenario():
        responses = {
            "host.web.search": {"results": [{"title": "t", "url": "https://e.x/a"}]},
            "host.web.fetch": {"pages": [{"url": "https://e.x/a", "content": "body"}]},
            "host.web.image_search": {"results": [{"image_url": "https://e.x/i.png"}]},
            "host.web.image_fetch": {"path": "web-images/i.png", "get_url": "https://r2/i"},
        }
        async with running(WEB, responses) as h:
            assert (await h.invoke("web_search", {"query": "anna"}))["results"]
            research = await h.invoke("web_research", {"query": "anna", "pages": 1})
            assert research["pages"][0]["content"] == "body"
            # The forum #598 request.
            images = await h.invoke("web_image_search", {"query": "万里长城 航拍", "max_results": 3})
            assert images["results"][0]["image_url"] == "https://e.x/i.png"
            grabbed = await h.invoke("web_image_grab", {"url": "https://e.x/i.png"})
            assert grabbed["get_url"] == "https://r2/i"
            return h.host_methods

    assert asyncio.run(scenario()) == [
        "host.web.search",
        "host.web.search",
        "host.web.fetch",
        "host.web.image_search",
        "host.web.image_fetch",
    ]


def test_clip_narrator_media_call_reaches_all_thirteen_media_methods():
    job = {"jobId": "job_1", "state": "queued"}
    calls = [
        ({"op": "submit", "kind": "image", "request": {"prompt": "a kite", "n": 1}}, "host.image.generateAsync"),
        (
            {"op": "submit", "kind": "image", "request": {"prompt": "red", "image_url": "https://e.x/i.png"}},
            "host.image.editAsync",
        ),
        ({"op": "get_job", "kind": "image", "job_id": "job_1"}, "host.image.getJob"),
        ({"op": "cancel_job", "kind": "image", "job_id": "job_1"}, "host.image.cancelJob"),
        ({"op": "resign", "request": {"r2Keys": ["k1"]}}, "host.image.resign"),
        ({"op": "submit", "kind": "video", "request": {"prompt": "p", "durationSec": 5}}, "host.video.generate"),
        ({"op": "get_job", "kind": "video", "job_id": "job_1"}, "host.video.getJob"),
        ({"op": "cancel_job", "kind": "video", "job_id": "job_1"}, "host.video.cancelJob"),
        (
            {"op": "submit", "kind": "music", "request": {"prompt": "p", "idempotencyKey": "k"}},
            "host.music.generate",
        ),
        ({"op": "get_job", "kind": "music", "job_id": "job_1"}, "host.music.getJob"),
        ({"op": "cancel_job", "kind": "music", "job_id": "job_1"}, "host.music.cancelJob"),
        ({"op": "speak", "request": {"text": "hi", "delivery": "url"}}, "host.audio.speak"),
        ({"op": "transcribe", "request": {"audioUrl": "https://e.x/a.mp3"}}, "host.audio.transcribe"),
    ]

    async def scenario():
        async with running(MEDIA, {method: job for _, method in calls}) as h:
            for args, _ in calls:
                assert await h.invoke("media_call", args) == {"op": args["op"], "ok": True, "result": job}
            return h.host_calls

    host_calls = asyncio.run(scenario())
    assert [method for method, _ in host_calls] == [method for _, method in calls]
    # Host API argument names reach the wire unchanged through the SDK.
    assert host_calls[5][1] == {"prompt": "p", "durationSec": 5}
    assert host_calls[8][1] == {"prompt": "p", "idempotencyKey": "k"}
    assert host_calls[2][1] == {"jobId": "job_1"}


def test_clip_narrator_reports_a_host_refusal_in_band():
    def refuse(_args: dict) -> dict:
        from anna_app_runtime_local.store import _HostCallError

        raise _HostCallError("APP_QUOTA_EXCEEDED", "quota exceeded")

    async def scenario():
        async with running(MEDIA, {"host.video.generate": refuse}) as h:
            return await h.invoke("media_call", {"op": "submit", "kind": "video", "request": {"prompt": "p"}})

    out = asyncio.run(scenario())
    assert out["ok"] is False
    assert out["error"]["data"]["errorCode"] == "APP_QUOTA_EXCEEDED"


def test_clip_narrator_await_tool_still_works():
    async def scenario():
        async with running(MEDIA, {"host.audio.speak": {"url": "https://r2/a.mp3", "charCount": 2}}) as h:
            out = await h.invoke("clip_narrate", {"script": "hi"})
            return out, h.host_methods

    out, methods = asyncio.run(scenario())
    assert out["audio_url"] == "https://r2/a.mp3"
    assert methods == ["host.audio.speak"]


def test_aps_files_demo_reads_quota_over_reverse_rpc():
    quota = {"used_bytes": 10, "quota_bytes": 100, "entries_count": 1, "percent": 0.1}

    async def scenario():
        async with running(FILES, {"host.storage.quota": quota}) as h:
            return await h.invoke("quota"), h.host_methods

    out, methods = asyncio.run(scenario())
    assert out == quota
    assert methods == ["host.storage.quota"]


def test_credentials_demo_reaches_both_credentials_methods():
    responses = {
        "host.credentials.listAccounts": {"accounts": [{"account_id": "acc_1", "email": "a@x.test"}]},
        "host.credentials.getToken": {"access_token": "not-a-real-token", "account_id": "acc_1"},
    }

    async def scenario():
        async with running(CREDENTIALS, responses) as h:
            listed = await h.invoke("list_accounts", {"provider": "google"})
            token = await h.invoke("token_info", {"provider": "google"})
            return listed, token, h.host_calls

    listed, token, host_calls = asyncio.run(scenario())
    assert listed["accounts"][0]["account_id"] == "acc_1"
    assert token["account_id"] == "acc_1"
    assert "not-a-real-token" not in str(token)  # the demo only ever returns it masked
    # The runtime, not the plugin, names the calling Executa.
    assert host_calls == [
        ("host.credentials.listAccounts", {"provider": "google", "tool_id": TOOL_ID}),
        ("host.credentials.getToken", {"provider": "google", "tool_id": TOOL_ID}),
    ]
