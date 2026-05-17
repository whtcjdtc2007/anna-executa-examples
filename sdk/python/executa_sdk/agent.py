"""Anna Executa Python SDK — Agent / App Session support

Brings stdio plugins to **parity** with anna-app iframes for LLM access:

- ``AgentSessionClient.create(...)``    →  reverse-RPC ``agent/session.create``
- ``AgentSession.run(content, ...)``    →  ``agent/session.run`` (buffered stream)
- ``AgentSession.cancel(run_id)``       →  ``agent/session.cancel``
- ``AgentSession.history()``            →  ``agent/session.history``
- ``AgentSession.delete()``             →  ``agent/session.delete``
- ``AgentSessionClient.complete(...)``  →  ``agent/complete`` (L1 stateless)

Wire / auth model (plugin POV):
- Plugin **never** sees an ``app_session_token`` — the host ``matrix``
  caches it internally, keyed by ``app_session_uuid``.
- ``run`` is *buffered* in v2: the host accumulates SSE frames and
  returns them as a list once ``done==True``. Iterate with
  ``async for frame in session.run(...)``.

Threading: shares the same dispatch infrastructure as
:class:`SamplingClient` — the plugin's stdin loop must call
:meth:`dispatch_response` on every JSON message that has no ``method``
field. A single shared :func:`dispatch_message` helper is provided for
plugins that mount both clients.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .sampling import (
    SAMPLING_ERR_NOT_NEGOTIATED,
    SAMPLING_ERR_TIMEOUT,
    SamplingError,
    _Pending,
    _write_frame,
)

# ─── Constants — keep in sync with matrix/src/executa/protocol.py ─────

METHOD_AGENT_SESSION_CREATE = "agent/session.create"
METHOD_AGENT_SESSION_RUN = "agent/session.run"
METHOD_AGENT_SESSION_CANCEL = "agent/session.cancel"
METHOD_AGENT_SESSION_HISTORY = "agent/session.history"
METHOD_AGENT_SESSION_DELETE = "agent/session.delete"
METHOD_AGENT_COMPLETE = "agent/complete"

# Mirror matrix/src/executa/protocol.py AGENT_ERR_*
AGENT_ERR_NOT_GRANTED = -32041
AGENT_ERR_SESSION_NOT_FOUND = -32042
AGENT_ERR_INVALID_REQUEST = -32043
AGENT_ERR_SUBMODE_MISMATCH = -32044
AGENT_ERR_QUOTA_EXCEEDED = -32045
AGENT_ERR_PROVIDER_ERROR = -32046
AGENT_ERR_RATE_LIMITED = -32047
AGENT_ERR_TOOL_NOT_GRANTED = -32048


class AgentError(SamplingError):
    """Specialization of :class:`SamplingError` for agent.* errors.

    Reuses the base class so existing ``except SamplingError`` blocks
    catch both surfaces — convenient since they share the same auth
    plumbing.
    """


# ─── Session handle returned by .create() ─────────────────────────────


@dataclass
class AgentSession:
    """Lightweight handle returned by :meth:`AgentSessionClient.create`.

    Holds the ``app_session_uuid`` plus convenience accessors to issue
    further reverse-RPC calls scoped to this session. The actual
    ``app_session_token`` is held server-side by the matrix host and is
    deliberately **never** exposed to the plugin.
    """

    uuid: str
    expires_in: int
    kind: str
    agent_submode: Optional[str]
    fixed_client_id: Optional[str]
    granted_tools: List[str]
    thread_id: Optional[str] = None
    _client: "AgentSessionClient" = None  # type: ignore[assignment]

    async def run(
        self,
        content: str,
        *,
        attachments: Optional[List[dict]] = None,
        recursion_limit: int = 8,
        run_id: Optional[str] = None,
        timeout: float = 300.0,
    ) -> AsyncIterator[dict]:
        """Run one agent turn and yield each SSE frame from the host.

        v2 wire format is *buffered*: the host returns a single
        ``{run_id, stream_id, frames: [...], final}`` response after the
        run completes. We yield each frame in order so callers can write
        the same ``async for frame in session.run(...)`` code that an
        anna-app's ``llm.runAgent()`` uses.
        """
        if self._client is None:
            raise RuntimeError("AgentSession was not created via AgentSessionClient")
        result = await self._client._call(
            METHOD_AGENT_SESSION_RUN,
            {
                "app_session_uuid": self.uuid,
                "content": content,
                "attachments": attachments,
                "recursion_limit": recursion_limit,
                "run_id": run_id,
            },
            timeout=timeout,
        )
        for frame in result.get("frames") or []:
            yield frame

    async def cancel(self, run_id: str) -> dict:
        return await self._client._call(
            METHOD_AGENT_SESSION_CANCEL,
            {"app_session_uuid": self.uuid, "run_id": run_id},
        )

    async def history(self) -> dict:
        return await self._client._call(
            METHOD_AGENT_SESSION_HISTORY,
            {"app_session_uuid": self.uuid},
        )

    async def delete(self) -> dict:
        return await self._client._call(
            METHOD_AGENT_SESSION_DELETE,
            {"app_session_uuid": self.uuid},
        )


# ─── Client (multiplexes pending reverse RPCs) ────────────────────────


class AgentSessionClient:
    """Issue reverse ``agent/*`` RPCs to the host.

    A single instance per process is enough; it multiplexes outstanding
    requests by their JSON-RPC ``id``. Constructed with the same
    ``write_frame`` callable as :class:`SamplingClient`, so plugins
    typically share the underlying stdout writer.
    """

    def __init__(self, *, write_frame: Callable[[dict], None] | None = None):
        self._write_frame = write_frame or _write_frame
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._disabled_reason: Optional[str] = None

    # — wiring (mirrors SamplingClient API) —

    def disable(self, reason: str) -> None:
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
                    AgentError(
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
            if not pending.future.done():
                err = msg.get("error")
                if err:
                    pending.future.set_exception(
                        AgentError(
                            code=int(err.get("code", -32603)),
                            message=str(err.get("message", "unknown error")),
                            data=err.get("data"),
                        )
                    )
                else:
                    pending.future.set_result(msg.get("result") or {})
        return True

    # — internal —

    async def _call(self, method: str, params: dict, *, timeout: float = 60.0) -> dict:
        if self._disabled_reason:
            raise AgentError(SAMPLING_ERR_NOT_NEGOTIATED, self._disabled_reason)
        loop = asyncio.get_running_loop()
        self._loop = loop
        req_id = uuid.uuid4().hex
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        future: asyncio.Future[dict] = loop.create_future()
        with self._lock:
            self._pending[req_id] = _Pending(future=future)
        envelope = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": clean}
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
            raise AgentError(
                SAMPLING_ERR_TIMEOUT,
                f"{method} timed out after {timeout}s",
            )

    # — public API —

    async def create(
        self,
        *,
        kind: str = "agent",
        agent_submode: str = "auto",
        fixed_client_id: Optional[str] = None,
        label: Optional[str] = None,
        quota_caps: Optional[dict] = None,
        ttl_seconds: int = 600,
        timeout: float = 30.0,
    ) -> AgentSession:
        """Mint an Anna App Session and return a typed handle.

        - ``kind="agent"`` (default) uses the LangGraph agent loop.
        - ``agent_submode="auto"`` lets the host's auto-router pick a
          tool from the user's grant; ``"fixed"`` requires
          ``fixed_client_id``.
        """
        result = await self._call(
            METHOD_AGENT_SESSION_CREATE,
            {
                "kind": kind,
                "agent_submode": agent_submode if kind == "agent" else None,
                "fixed_client_id": fixed_client_id,
                "label": label,
                "quota_caps": quota_caps,
                "ttl_seconds": ttl_seconds,
            },
            timeout=timeout,
        )
        sess = AgentSession(
            uuid=result["app_session_uuid"],
            expires_in=int(result.get("expires_in") or ttl_seconds),
            kind=str(result.get("kind") or kind),
            agent_submode=result.get("agent_submode"),
            fixed_client_id=result.get("fixed_client_id"),
            granted_tools=list(result.get("granted_tools") or []),
            thread_id=result.get("thread_id"),
        )
        sess._client = self
        return sess

    async def complete(
        self,
        *,
        messages: List[dict],
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
        model_preferences: Optional[dict] = None,
        metadata: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        """L1 stateless completion (mirrors anna-app SDK ``llm.complete``).

        Returns the MCP-shaped completion dict, e.g.::

            {"role": "assistant", "content": {"type": "text", "text": "..."},
             "model": "gpt-4o-mini", "stopReason": "endTurn",
             "usage": {"inputTokens": ..., "outputTokens": ...}}
        """
        body: Dict[str, Any] = {"messages": messages}
        if max_tokens is not None:
            body["maxTokens"] = max_tokens
        if system_prompt is not None:
            body["systemPrompt"] = system_prompt
        if temperature is not None:
            body["temperature"] = temperature
        if stop_sequences:
            body["stopSequences"] = stop_sequences
        if model_preferences:
            body["modelPreferences"] = model_preferences
        if metadata:
            body["metadata"] = metadata
        return await self._call(METHOD_AGENT_COMPLETE, body, timeout=timeout)


__all__ = [
    "METHOD_AGENT_SESSION_CREATE",
    "METHOD_AGENT_SESSION_RUN",
    "METHOD_AGENT_SESSION_CANCEL",
    "METHOD_AGENT_SESSION_HISTORY",
    "METHOD_AGENT_SESSION_DELETE",
    "METHOD_AGENT_COMPLETE",
    "AGENT_ERR_NOT_GRANTED",
    "AGENT_ERR_SESSION_NOT_FOUND",
    "AGENT_ERR_INVALID_REQUEST",
    "AGENT_ERR_SUBMODE_MISMATCH",
    "AGENT_ERR_QUOTA_EXCEEDED",
    "AGENT_ERR_PROVIDER_ERROR",
    "AGENT_ERR_RATE_LIMITED",
    "AGENT_ERR_TOOL_NOT_GRANTED",
    "AgentError",
    "AgentSession",
    "AgentSessionClient",
]
