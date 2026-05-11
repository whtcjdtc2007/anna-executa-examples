"""Anna Executa Python SDK — Persistent Storage support

`StorageClient` and `FilesClient` let an Executa plugin issue reverse
JSON-RPC requests to its host Agent that proxy to **Anna Persistent
Storage** (APS) — Anna's cross-Agent / cross-App / cross-Tool key-value
+ object store with per-user 5GB default quota.

Wire protocol — Plugin → Agent → Nexus REST:

    Plugin (us)                                Agent (host)                       Nexus
    ────────────────────────────────────────────────────────────────────────────────────
    invoke(req_id=42, …)              ◄── (host called us)
    storage/get(req_id=A, key=…)      ──► (we ask host for KV)
                                          GET /api/v1/storage/kv?scope=…
                                          ◄── 200 {value, etag, …}
    ◄── result | error                ──┘
    invoke result(req_id=42)          ──► (we finish original tool)

This module is **transport-compatible** with :mod:`executa_sdk.sampling`:
both clients write to the same stdout JSON-RPC stream and route
responses by ``id``. A single stdin reader loop should:

1. Try ``sampling_client.dispatch_response(msg)`` first.
2. Fall back to ``storage_client.dispatch_response(msg)`` /
   ``files_client.dispatch_response(msg)``.

Or use :func:`make_response_router` to wire them all in one call.

Threading / async model is identical to :class:`SamplingClient`.

Error codes — keep in sync with ``matrix/src/executa/protocol.py``::

    STORAGE_ERR_NOT_GRANTED        = -32021
    STORAGE_ERR_NOT_FOUND          = -32022
    STORAGE_ERR_PRECONDITION_FAILED = -32023
    STORAGE_ERR_QUOTA_EXCEEDED     = -32024
    STORAGE_ERR_VALUE_TOO_LARGE    = -32025
    STORAGE_ERR_RATE_LIMITED       = -32026
    STORAGE_ERR_INVALID_PATH       = -32027
    STORAGE_ERR_INVALID_REQUEST    = -32028
    STORAGE_ERR_UPSTREAM           = -32029
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .sampling import _write_frame  # reuse existing frame writer


# ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

METHOD_STORAGE_GET = "storage/get"
METHOD_STORAGE_SET = "storage/set"
METHOD_STORAGE_DELETE = "storage/delete"
METHOD_STORAGE_LIST = "storage/list"

METHOD_FILES_UPLOAD_BEGIN = "files/upload_begin"
METHOD_FILES_UPLOAD_COMPLETE = "files/upload_complete"
METHOD_FILES_DOWNLOAD_URL = "files/download_url"
METHOD_FILES_LIST = "files/list"
METHOD_FILES_DELETE = "files/delete"

METHOD_USER_FILES_UPLOAD_BEGIN = "user_files/upload_begin"
METHOD_USER_FILES_UPLOAD_COMPLETE = "user_files/upload_complete"
METHOD_USER_FILES_DOWNLOAD_URL = "user_files/download_url"
METHOD_USER_FILES_LIST = "user_files/list"
METHOD_USER_FILES_DELETE = "user_files/delete"


# ─── Error codes ──────────────────────────────────────────────────────

STORAGE_ERR_NOT_GRANTED = -32021
STORAGE_ERR_NOT_FOUND = -32022
STORAGE_ERR_PRECONDITION_FAILED = -32023
STORAGE_ERR_QUOTA_EXCEEDED = -32024
STORAGE_ERR_VALUE_TOO_LARGE = -32025
STORAGE_ERR_RATE_LIMITED = -32026
STORAGE_ERR_INVALID_PATH = -32027
STORAGE_ERR_INVALID_REQUEST = -32028
STORAGE_ERR_UPSTREAM = -32029
STORAGE_ERR_TIMEOUT = -32030  # SDK-local: generated when await times out


class StorageError(Exception):
    """Wraps a JSON-RPC error returned by the host for ``storage/*`` /
    ``files/*`` reverse RPCs."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


# ─── Internal request dispatcher ──────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class _BaseRpcClient:
    """Shared plumbing for reverse-RPC clients with no streaming results.

    Subclasses just call :meth:`_call(method, params, timeout)` to issue
    a request and await the host result dict.
    """

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
        """Mark the namespace as unavailable (e.g. host did not negotiate v2)."""
        self._disabled_reason = reason

    def is_response_envelope(self, msg: dict) -> bool:
        if not isinstance(msg, dict) or "method" in msg:
            return False
        return "id" in msg and msg.get("id") in self._pending

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
                    StorageError(
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

    # — internal —

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        if self._disabled_reason:
            raise StorageError(STORAGE_ERR_NOT_GRANTED, self._disabled_reason)
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
            "params": params,
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
            raise StorageError(
                STORAGE_ERR_TIMEOUT,
                f"{method} timed out after {timeout}s",
            )


# ─── Public clients ───────────────────────────────────────────────────


class StorageClient(_BaseRpcClient):
    """Cross-Agent / App / Tool key-value storage.

    Each method returns the host result dict verbatim. Failures raise
    :class:`StorageError`.

    Default scope is ``"app"`` — i.e. shared across all invocations of the
    same Anna App for the same user. Pass ``scope="user"`` to access the
    user-wide namespace, or ``scope="tool"`` for tool-private keys.
    """

    DEFAULT_TIMEOUT = 30.0

    async def get(
        self, key: str, *, scope: str = "app", timeout: float = DEFAULT_TIMEOUT
    ) -> dict:
        """Read ``key``. Returns ``{"value": …, "etag": "…", "exists": bool}``.

        Missing keys resolve to ``{"value": None, "exists": False}`` rather
        than raising — the host normalises 404 into this shape.
        """
        return await self._call(
            METHOD_STORAGE_GET, {"key": key, "scope": scope}, timeout
        )

    async def set(
        self,
        key: str,
        value: Any,
        *,
        scope: str = "app",
        if_match: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Write ``key=value``. Returns ``{"etag", "generation", "size_bytes"}``.

        Pass ``if_match=<etag>`` for optimistic concurrency control;
        mismatches raise :class:`StorageError` with code
        :data:`STORAGE_ERR_PRECONDITION_FAILED`.
        """
        params: Dict[str, Any] = {"key": key, "value": value, "scope": scope}
        if if_match is not None:
            params["if_match"] = if_match
        if ttl_seconds is not None:
            params["ttl_seconds"] = ttl_seconds
        return await self._call(METHOD_STORAGE_SET, params, timeout)

    async def delete(
        self,
        key: str,
        *,
        scope: str = "app",
        if_match: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Delete ``key``. Returns ``{"deleted": True}`` (idempotent)."""
        params: Dict[str, Any] = {"key": key, "scope": scope}
        if if_match is not None:
            params["if_match"] = if_match
        return await self._call(METHOD_STORAGE_DELETE, params, timeout)

    async def list(
        self,
        *,
        prefix: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Paginate keys/files. Returns ``{"items": [...], "next_cursor": …}``.

        ``kind`` may be ``"kv"`` or ``"file"`` to constrain the listing.
        """
        params: Dict[str, Any] = {"scope": scope}
        if prefix is not None:
            params["prefix"] = prefix
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        if kind is not None:
            params["kind"] = kind
        return await self._call(METHOD_STORAGE_LIST, params, timeout)


class FilesClient(_BaseRpcClient):
    """Cross-Agent / App / Tool object storage hosted by Anna.

    Two-step upload contract:

        info = await files.upload_begin(path="reports/q3.pdf",
                                         size_bytes=os.path.getsize(p),
                                         content_type="application/pdf")
        # 1. PUT the bytes to info["put_url"] with info["headers"]
        # 2. Tell host the upload finished:
        await files.upload_complete(path="reports/q3.pdf",
                                     etag=resp.headers["etag"],
                                     size_bytes=os.path.getsize(p))

    Default scope is ``"app"``. To access the user-wide file namespace
    (e.g. files the user uploaded via the Anna chat UI) pass
    ``scope="user"`` — the host then routes via ``user_files/*`` endpoints.
    """

    DEFAULT_TIMEOUT = 60.0

    def _route(self, base: str, scope: str) -> str:
        if scope == "user":
            return base.replace("files/", "user_files/", 1)
        return base

    async def upload_begin(
        self,
        *,
        path: str,
        size_bytes: Optional[int] = None,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Returns ``{"upload_id", "put_url", "headers", "expires_at"}``."""
        params: Dict[str, Any] = {"path": path, "scope": scope}
        if size_bytes is not None:
            params["size_bytes"] = size_bytes
        if content_type is not None:
            params["content_type"] = content_type
        if metadata is not None:
            params["metadata"] = metadata
        return await self._call(self._route(METHOD_FILES_UPLOAD_BEGIN, scope), params, timeout)

    async def upload_complete(
        self,
        *,
        path: str,
        etag: Optional[str] = None,
        size_bytes: Optional[int] = None,
        content_type: Optional[str] = None,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Confirm upload to host (host verifies the object landed in storage)."""
        params: Dict[str, Any] = {"path": path, "scope": scope}
        if etag is not None:
            params["etag"] = etag
        if size_bytes is not None:
            params["size_bytes"] = size_bytes
        if content_type is not None:
            params["content_type"] = content_type
        return await self._call(self._route(METHOD_FILES_UPLOAD_COMPLETE, scope), params, timeout)

    async def download_url(
        self,
        *,
        path: str,
        expires_in: Optional[int] = None,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Returns ``{"url": "https://signed-download-url", "expires_at": "..."}``."""
        params: Dict[str, Any] = {"path": path, "scope": scope}
        if expires_in is not None:
            params["expires_in"] = expires_in
        return await self._call(self._route(METHOD_FILES_DOWNLOAD_URL, scope), params, timeout)

    async def list(
        self,
        *,
        prefix: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Returns ``{"items": [{"path","size_bytes","content_type","updated_at"}], "next_cursor": …}``."""
        params: Dict[str, Any] = {"scope": scope}
        if prefix is not None:
            params["prefix"] = prefix
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return await self._call(self._route(METHOD_FILES_LIST, scope), params, timeout)

    async def delete(
        self,
        *,
        path: str,
        scope: str = "app",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Returns ``{"deleted": True}`` (idempotent)."""
        return await self._call(
            self._route(METHOD_FILES_DELETE, scope), {"path": path, "scope": scope}, timeout
        )


# ─── Multiplexer ──────────────────────────────────────────────────────


def make_response_router(
    *clients: _BaseRpcClient,
) -> Callable[[dict], bool]:
    """Build a single dispatch fn that routes an inbound message to whichever
    client has a matching pending request.

    Use in your stdin reader loop::

        from executa_sdk.sampling import SamplingClient
        from executa_sdk.storage import StorageClient, FilesClient, make_response_router

        sampling = SamplingClient()
        storage = StorageClient()
        files = FilesClient()
        route = make_response_router(sampling, storage, files)

        for msg in incoming_frames:
            if "method" in msg:
                ...  # host-initiated request (invoke / shutdown / ...)
            else:
                route(msg)
    """

    def _route(msg: dict) -> bool:
        for c in clients:
            if c.dispatch_response(msg):
                return True
        return False

    return _route


__all__ = [
    "StorageClient",
    "FilesClient",
    "StorageError",
    "make_response_router",
    "METHOD_STORAGE_GET",
    "METHOD_STORAGE_SET",
    "METHOD_STORAGE_DELETE",
    "METHOD_STORAGE_LIST",
    "METHOD_FILES_UPLOAD_BEGIN",
    "METHOD_FILES_UPLOAD_COMPLETE",
    "METHOD_FILES_DOWNLOAD_URL",
    "METHOD_FILES_LIST",
    "METHOD_FILES_DELETE",
    "METHOD_USER_FILES_UPLOAD_BEGIN",
    "METHOD_USER_FILES_UPLOAD_COMPLETE",
    "METHOD_USER_FILES_DOWNLOAD_URL",
    "METHOD_USER_FILES_LIST",
    "METHOD_USER_FILES_DELETE",
    "STORAGE_ERR_NOT_GRANTED",
    "STORAGE_ERR_NOT_FOUND",
    "STORAGE_ERR_PRECONDITION_FAILED",
    "STORAGE_ERR_QUOTA_EXCEEDED",
    "STORAGE_ERR_VALUE_TOO_LARGE",
    "STORAGE_ERR_RATE_LIMITED",
    "STORAGE_ERR_INVALID_PATH",
    "STORAGE_ERR_INVALID_REQUEST",
    "STORAGE_ERR_UPSTREAM",
    "STORAGE_ERR_TIMEOUT",
]
