"""Tests for executa_sdk.context — invoke binding + reverse-RPC stamping.

Covers the forum #188 fix: SDK clients must stamp
``params.context.invoke_id`` on outgoing reverse RPCs so the host can
correlate them with the parent invoke when multiple invokes run
concurrently.
"""

from __future__ import annotations

import asyncio

import pytest

from executa_sdk import (
    HostUploadClient,
    InvokeContext,
    attach_invoke_context,
    bind_invoke,
    get_current_invoke_id,
)


# ─── bind/attach primitives ───────────────────────────────────────────


def test_unbound_attach_is_noop():
    params = {"mode": "confirm", "r2_key": "k"}
    assert attach_invoke_context(params) is params
    assert "context" not in params


def test_bind_invoke_with_params_extracts_invoke_id():
    invoke_params = {"tool": "t", "arguments": {}, "context": {"invoke_id": "iv-1"}}
    with bind_invoke(invoke_params) as iv:
        assert iv == "iv-1"
        assert get_current_invoke_id() == "iv-1"
        params = attach_invoke_context({"mode": "negotiate"})
        assert params["context"] == {"invoke_id": "iv-1"}
    assert get_current_invoke_id() is None


def test_bind_invoke_with_string():
    with bind_invoke("iv-str"):
        assert get_current_invoke_id() == "iv-str"


def test_attach_does_not_overwrite_explicit_invoke_id():
    with bind_invoke("iv-ambient"):
        params = attach_invoke_context({"context": {"invoke_id": "iv-explicit"}})
        assert params["context"]["invoke_id"] == "iv-explicit"


def test_bind_invoke_nesting_restores_outer():
    with bind_invoke("outer"):
        with bind_invoke("inner"):
            assert get_current_invoke_id() == "inner"
        assert get_current_invoke_id() == "outer"


def test_invoke_context_from_params_still_parses():
    ctx = InvokeContext.from_params(
        {"context": {"invoke_id": "iv-2", "deadline_ms": 1000}}
    )
    assert ctx.invoke_id == "iv-2"
    assert ctx.deadline_ms == 1000


# ─── concurrent task isolation (the actual race) ─────────────────────


def test_concurrent_tasks_each_see_their_own_binding():
    seen: dict[str, str | None] = {}

    async def handler(invoke_id: str):
        with bind_invoke(invoke_id):
            await asyncio.sleep(0.01)  # force interleaving
            seen[invoke_id] = get_current_invoke_id()

    async def main():
        await asyncio.gather(*(handler(f"iv-{i}") for i in range(5)))

    asyncio.run(main())
    assert seen == {f"iv-{i}": f"iv-{i}" for i in range(5)}


# ─── client integration: frames carry context.invoke_id ──────────────


def test_host_upload_client_stamps_context_on_reverse_rpc():
    frames: list[dict] = []
    client = HostUploadClient(write_frame=frames.append)

    async def run():
        with bind_invoke("iv-up"):
            task = asyncio.ensure_future(client.confirm(r2_key="k", timeout=1.0))
            await asyncio.sleep(0)  # let the frame get written
            # resolve the pending future so the call completes
            req_id = frames[0]["id"]
            client.dispatch_response(
                {"jsonrpc": "2.0", "id": req_id, "result": {"r2_key": "k"}}
            )
            await task

    asyncio.run(run())
    assert frames[0]["method"] == "host/uploadFile"
    assert frames[0]["params"]["context"] == {"invoke_id": "iv-up"}
    assert frames[0]["params"]["mode"] == "confirm"


def test_host_upload_client_unbound_frame_has_no_context():
    frames: list[dict] = []
    client = HostUploadClient(write_frame=frames.append)

    async def run():
        task = asyncio.ensure_future(client.confirm(r2_key="k", timeout=1.0))
        await asyncio.sleep(0)
        client.dispatch_response(
            {"jsonrpc": "2.0", "id": frames[0]["id"], "result": {"r2_key": "k"}}
        )
        await task

    asyncio.run(run())
    assert "context" not in frames[0]["params"]


def test_task_started_in_a_handler_detaches_with_bind_invoke_none():
    """A task created inside a handler inherits its binding; work meant to
    outlive the invoke detaches so the host treats it as background."""
    frames: list[dict] = []
    client = HostUploadClient(write_frame=frames.append)

    async def confirm(r2_key: str):
        task = asyncio.ensure_future(client.confirm(r2_key=r2_key, timeout=1.0))
        await asyncio.sleep(0)
        frame = next(f for f in frames if f["params"]["r2_key"] == r2_key)
        client.dispatch_response(
            {"jsonrpc": "2.0", "id": frame["id"], "result": {"r2_key": r2_key}}
        )
        await task

    async def detached():
        with bind_invoke(None):
            await confirm("detached")

    async def handler():
        with bind_invoke("iv-1"):
            inherited = asyncio.ensure_future(confirm("inherited"))
            background = asyncio.ensure_future(detached())
        await asyncio.gather(inherited, background)

    asyncio.run(handler())
    by_key = {f["params"]["r2_key"]: f["params"].get("context") for f in frames}
    assert by_key == {"inherited": {"invoke_id": "iv-1"}, "detached": None}


def test_two_concurrent_uploads_carry_distinct_invoke_ids():
    """The forum #188 scenario: overlapping invokes → each reverse RPC
    must carry ITS OWN parent invoke_id."""
    frames: list[dict] = []
    client = HostUploadClient(write_frame=frames.append)

    async def one_upload(invoke_id: str):
        with bind_invoke(invoke_id):
            task = asyncio.ensure_future(client.confirm(r2_key=invoke_id, timeout=1.0))
            await asyncio.sleep(0.01)
            frame = next(f for f in frames if f["params"]["r2_key"] == invoke_id)
            client.dispatch_response(
                {"jsonrpc": "2.0", "id": frame["id"], "result": {"r2_key": invoke_id}}
            )
            await task

    async def main():
        await asyncio.gather(one_upload("iv-A"), one_upload("iv-B"))

    asyncio.run(main())
    by_key = {f["params"]["r2_key"]: f["params"]["context"]["invoke_id"] for f in frames}
    assert by_key == {"iv-A": "iv-A", "iv-B": "iv-B"}
