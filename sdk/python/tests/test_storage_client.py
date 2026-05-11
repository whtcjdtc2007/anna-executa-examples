"""Smoke tests for executa_sdk.storage StorageClient/FilesClient.

These cover the request envelope shape + dispatch_response routing, with
a fake "host" that immediately responds via the same dispatcher. No real
stdin/stdout is exercised.

Run with: ``pytest -q``  (from anna-executa-examples/sdk/python).
"""

from __future__ import annotations

import asyncio
import pytest

from executa_sdk.storage import (
    FilesClient,
    METHOD_FILES_DOWNLOAD_URL,
    METHOD_FILES_UPLOAD_BEGIN,
    METHOD_STORAGE_GET,
    METHOD_STORAGE_LIST,
    METHOD_STORAGE_SET,
    METHOD_USER_FILES_DOWNLOAD_URL,
    STORAGE_ERR_QUOTA_EXCEEDED,
    StorageClient,
    StorageError,
    make_response_router,
)


class _FakeHost:
    """Captures envelopes and lets us synthesise responses."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    def write(self, msg: dict) -> None:
        self.frames.append(msg)


@pytest.mark.asyncio
async def test_storage_get_envelope_shape():
    host = _FakeHost()
    client = StorageClient(write_frame=host.write)
    task = asyncio.create_task(client.get("user.profile"))
    # Let the request be sent.
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["jsonrpc"] == "2.0"
    assert env["method"] == METHOD_STORAGE_GET
    assert env["params"] == {"key": "user.profile", "scope": "app"}
    # Synthesise host reply.
    client.dispatch_response(
        {"jsonrpc": "2.0", "id": env["id"], "result": {"value": 1, "exists": True}}
    )
    out = await task
    assert out == {"value": 1, "exists": True}


@pytest.mark.asyncio
async def test_storage_set_with_if_match_and_ttl():
    host = _FakeHost()
    client = StorageClient(write_frame=host.write)
    task = asyncio.create_task(
        client.set("k", {"v": 1}, if_match="e1", ttl_seconds=120)
    )
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_STORAGE_SET
    assert env["params"] == {
        "key": "k",
        "value": {"v": 1},
        "scope": "app",
        "if_match": "e1",
        "ttl_seconds": 120,
    }
    client.dispatch_response(
        {"jsonrpc": "2.0", "id": env["id"], "result": {"etag": "e2"}}
    )
    assert (await task) == {"etag": "e2"}


@pytest.mark.asyncio
async def test_storage_list_passes_pagination():
    host = _FakeHost()
    client = StorageClient(write_frame=host.write)
    task = asyncio.create_task(
        client.list(prefix="logs/", cursor="c1", limit=10, kind="kv")
    )
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_STORAGE_LIST
    assert env["params"] == {
        "scope": "app",
        "prefix": "logs/",
        "cursor": "c1",
        "limit": 10,
        "kind": "kv",
    }
    client.dispatch_response(
        {"jsonrpc": "2.0", "id": env["id"], "result": {"items": [], "next_cursor": None}}
    )
    await task


@pytest.mark.asyncio
async def test_files_upload_begin():
    host = _FakeHost()
    client = FilesClient(write_frame=host.write)
    task = asyncio.create_task(
        client.upload_begin(
            path="reports/q3.pdf", size_bytes=1024, content_type="application/pdf"
        )
    )
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_FILES_UPLOAD_BEGIN
    assert env["params"]["path"] == "reports/q3.pdf"
    assert env["params"]["size_bytes"] == 1024
    assert env["params"]["content_type"] == "application/pdf"
    client.dispatch_response(
        {
            "jsonrpc": "2.0",
            "id": env["id"],
            "result": {"upload_id": "u1", "put_url": "https://upload.example/..."},
        }
    )
    out = await task
    assert out["upload_id"] == "u1"


@pytest.mark.asyncio
async def test_files_user_scope_routes_to_user_files():
    host = _FakeHost()
    client = FilesClient(write_frame=host.write)
    task = asyncio.create_task(
        client.download_url(path="Documents/contract.pdf", scope="user")
    )
    await asyncio.sleep(0)
    env = host.frames[0]
    assert env["method"] == METHOD_USER_FILES_DOWNLOAD_URL
    client.dispatch_response(
        {"jsonrpc": "2.0", "id": env["id"], "result": {"url": "https://signed"}}
    )
    await task


@pytest.mark.asyncio
async def test_error_envelope_raises_storage_error():
    host = _FakeHost()
    client = StorageClient(write_frame=host.write)
    task = asyncio.create_task(client.set("k", "v"))
    await asyncio.sleep(0)
    env = host.frames[0]
    client.dispatch_response(
        {
            "jsonrpc": "2.0",
            "id": env["id"],
            "error": {
                "code": STORAGE_ERR_QUOTA_EXCEEDED,
                "message": "5GB cap",
            },
        }
    )
    with pytest.raises(StorageError) as exc:
        await task
    assert exc.value.code == STORAGE_ERR_QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_make_response_router_routes_to_owner():
    host = _FakeHost()
    storage = StorageClient(write_frame=host.write)
    files = FilesClient(write_frame=host.write)
    router = make_response_router(storage, files)

    t1 = asyncio.create_task(storage.get("k"))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(files.download_url(path="x.txt"))
    await asyncio.sleep(0)
    e1, e2 = host.frames
    # Route in arbitrary order.
    assert router({"jsonrpc": "2.0", "id": e2["id"], "result": {"url": "https://a"}})
    assert router({"jsonrpc": "2.0", "id": e1["id"], "result": {"value": 1, "exists": True}})
    # Unknown id → False
    assert not router({"jsonrpc": "2.0", "id": "nope", "result": {}})
    out1 = await t1
    out2 = await t2
    assert out1 == {"value": 1, "exists": True}
    assert out2 == {"url": "https://a"}


@pytest.mark.asyncio
async def test_disabled_client_raises_not_granted():
    client = StorageClient()
    client.disable("host did not negotiate v2")
    with pytest.raises(StorageError) as exc:
        await client.get("k")
    assert exc.value.code == -32021  # STORAGE_ERR_NOT_GRANTED
