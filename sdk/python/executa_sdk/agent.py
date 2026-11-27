"""Anna Executa Python SDK — Agent / App Session support

Brings stdio plugins to **parity** with anna-app iframes for LLM access:

- ``AgentSessionClient.create(...)``    →  reverse-RPC ``agent/session.create``
- ``AgentSessionClient.attach(uuid)``   →  ``agent/session.refresh`` + handle for an existing session
- ``AgentSessionClient.catalog()``      →  ``agent/session.catalog`` (which tools a session would get)
- ``AgentSession.run(content, ...)``    →  ``agent/session.run`` (frames delivered live)
- ``AgentSession.cancel(run_id)``       →  ``agent/session.cancel``
- ``AgentSession.history()``            →  ``agent/session.history``
- ``AgentSession.refresh(...)``         →  ``agent/session.refresh`` (re-mint token + slide idle window)
- ``AgentSession.delete()``             →  ``agent/session.delete``
- ``AgentSession.run_status(run_id)``   →  ``agent/session.run.status`` (did it execute/bill? forum #341)
- ``AgentSessionClient.frames(...)``    →  ``agent/session.frames`` (archived frame replay by seq)
- ``AgentSessionClient.complete(...)``  →  ``agent/complete`` (L1 stateless)

Wire / auth model (plugin POV):
- Plugin **never** sees an ``app_session_token`` — the host ``matrix``
  caches it internally, keyed by ``app_session_uuid``.
- ``run`` delivers frames **while the run is in flight**: the host pushes
  each one as an ``agent/session.frame`` notification and still answers
  with the complete ``frames`` list, which stays authoritative. Iterate
  with ``async for frame in session.run(...)``. Against a host that does
  not push (or with an unwired stdin loop, see below) the same code simply
  receives every frame when the run completes.

Threading: shares the same dispatch infrastructure as
:class:`SamplingClient` — the plugin's stdin loop must call
:meth:`dispatch_response` on every JSON message that has no ``method``
field, and :meth:`AgentSessionClient.dispatch_notification` on the ones
that do (live frames)::

    if "method" not in msg:
        ...  # route the reverse-RPC response
    elif agent_client.dispatch_notification(msg):
        return
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .context import attach_invoke_context
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
METHOD_AGENT_SESSION_LIST = "agent/session.list"
METHOD_AGENT_SESSION_REFRESH = "agent/session.refresh"
METHOD_AGENT_SESSION_RUN_STATUS = "agent/session.run.status"
METHOD_AGENT_SESSION_FRAMES = "agent/session.frames"
METHOD_AGENT_SESSION_CATALOG = "agent/session.catalog"
METHOD_AGENT_COMPLETE = "agent/complete"
# Host → plugin notification (no ``id``): one live frame of an in-flight run.
METHOD_AGENT_SESSION_FRAME = "agent/session.frame"

# Queue sentinel: the ``run`` response (or its error) has arrived.
_RUN_DONE = object()

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
    # True when the session inherits the user's host tools (files, browser,
    # commands…) — in which case granted_tools is the ["*"] sentinel. When
    # False with an EMPTY granted_tools, the session is TEXT-ONLY: it cannot
    # touch local files and any claimed side effects are hallucinated.
    # Hosts < 1.1.0-beta.45 omit the field; we then infer it from "*".
    inherit_host_tools: bool = False
    thread_id: Optional[str] = None
    system_prompt: Optional[str] = None
    _client: "AgentSessionClient" = None  # type: ignore[assignment]

    async def run(
        self,
        content: str,
        *,
        attachments: Optional[List[dict]] = None,
        recursion_limit: int = 8,
        run_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model_preferences: Optional[dict] = None,
        allowed_tools: Optional[List[str]] = None,
        timeout: float = 300.0,
    ) -> AsyncIterator[dict]:
        """Run one agent turn and yield each frame as the host produces it.

        The host pushes frames live (``agent/session.frame``) and still
        answers with the complete ``{run_id, stream_id, frames: [...],
        final}`` response. Frames are yielded in ``seq`` order as soon as
        they form a contiguous prefix; whatever was not delivered live is
        yielded from the response, so the sequence you iterate always equals
        the response's ``frames`` — no duplicates, no gaps — whether or not
        the host pushes at all. A failed run raises :class:`AgentError`
        after the frames that had already arrived.

        Leaving the loop early (``break`` / exception) cancels the upstream
        run on a best-effort basis so it stops consuming quota.

        ``system_prompt`` (optional) overrides the session-level system
        prompt for **this turn only**; it does not change the stored
        session value. Subject to the platform safety floor.

        ``model_preferences`` (optional) is the MCP-shaped
        ``{"hints": [{"name": ...}], "costPriority": ..., ...}`` dict,
        applied to **this turn only**. It is a *soft* preference: the
        host substring-matches hints against its active models and falls
        back to the user's saved model when nothing matches — it never
        fails the run. Same semantics as ``sampling/createMessage``.

        ``allowed_tools`` (optional) narrows THIS run's tool surface to a
        subset of the session's granted tools (sandbox sessions only —
        inherit-host-tools sessions keep the full host kit regardless).
        """
        if self._client is None:
            raise RuntimeError("AgentSession was not created via AgentSessionClient")
        # ``run_id`` is the only key tying live frames to this run before the
        # response arrives, so it is always sent.
        run_id = run_id or f"run_{uuid.uuid4().hex[:16]}"
        body = {
            "app_session_uuid": self.uuid,
            "content": content,
            "attachments": attachments,
            "recursion_limit": recursion_limit,
            "run_id": run_id,
            "stream": True,
        }
        if system_prompt is not None:
            body["systemPrompt"] = system_prompt
        if model_preferences:
            body["modelPreferences"] = model_preferences
        if allowed_tools:
            body["allowed_tools"] = list(allowed_tools)
        client = self._client
        queue: asyncio.Queue = asyncio.Queue()
        with client._lock:
            client._streams[run_id] = queue
        call = asyncio.ensure_future(
            client._call(METHOD_AGENT_SESSION_RUN, body, timeout=timeout)
        )
        call.add_done_callback(lambda _t: queue.put_nowait(_RUN_DONE))

        next_seq = 0
        early: Dict[int, dict] = {}
        finished = False
        try:
            while True:
                item = await queue.get()
                if item is _RUN_DONE:
                    break
                seq = item.get("seq")
                if isinstance(seq, int) and seq >= next_seq:
                    early[seq] = item.get("frame")
                # Reorder by seq: a plugin may read stdin on a thread pool.
                while next_seq in early:
                    yield early.pop(next_seq)
                    next_seq += 1
            result = call.result()  # a failed run raises AgentError here
            for frame in (result.get("frames") or [])[next_seq:]:
                yield frame
            finished = True
        finally:
            with client._lock:
                client._streams.pop(run_id, None)
            if not finished and not call.done():
                # The caller left the loop early: stop waiting, and stop the
                # upstream run so it does not keep burning quota.
                call.cancel()
                try:
                    await self.cancel(run_id)
                except Exception:
                    pass

    async def cancel(self, run_id: str) -> dict:
        return await self._client._call(
            METHOD_AGENT_SESSION_CANCEL,
            {"app_session_uuid": self.uuid, "run_id": run_id},
        )

    async def run_status(self, run_id: str, *, timeout: float = 15.0) -> dict:
        """Point-in-time status of one run (push pairs with pull, forum #341).

        Check this BEFORE re-running after a broken/timed-out ``run()`` —
        ``"completed"`` means the run executed and was billed; recover its
        output via :meth:`AgentSessionClient.frames` (use the ``stream_id``
        from THIS response — the one echoed by ``run()`` is host-local and
        not valid for frame replay). Statuses: ``queued | running |
        completed | failed | cancelled``; unknown / foreign / aged-out
        (>1h) runs raise ``AGENT_ERR_SESSION_NOT_FOUND``-class errors.
        """
        return await self._client._call(
            METHOD_AGENT_SESSION_RUN_STATUS,
            {"app_session_uuid": self.uuid, "run_id": run_id},
            timeout=timeout,
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

    async def refresh(self, *, ttl_seconds: int = 600) -> dict:
        """Re-mint this session's token and slide its idle window.

        Identity-scoped on the invoke ``sampling_token`` host-side, so it
        recovers a session even after the host's in-memory token cache
        lapsed (process restart / long idle). Updates ``self.expires_in``
        from the response and returns the fresh lifecycle metadata
        (``expires_at`` / ``max_lifetime_at`` / ``idle_ttl_seconds`` / ...).
        """
        res = await self._client.refresh(
            self.uuid, ttl_seconds=ttl_seconds
        )
        if isinstance(res, dict) and res.get("expires_in") is not None:
            try:
                self.expires_in = int(res["expires_in"])
            except (TypeError, ValueError):
                pass
        return res


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
        # In-flight runs awaiting live frames, keyed by ``run_id``.
        self._streams: Dict[str, asyncio.Queue] = {}

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

    def dispatch_notification(self, msg: dict) -> bool:
        """Route a host → plugin notification (a live ``agent/session.frame``).

        Call it from the stdin loop for every message that carries a
        ``method``. Returns True when the message was an agent frame — even
        if no run is waiting for it any more — so the loop can stop looking
        for another handler. Safe to call from any thread.
        """
        if not isinstance(msg, dict) or msg.get("method") != METHOD_AGENT_SESSION_FRAME:
            return False
        params = msg.get("params")
        if not isinstance(params, dict):
            return True
        with self._lock:
            queue = self._streams.get(params.get("run_id"))
        loop = self._loop
        if queue is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, params)
            except RuntimeError:
                pass  # loop closed — the response path still delivers every frame
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
        envelope = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": attach_invoke_context(clean)}
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
        system_prompt: Optional[str] = None,
        ttl_seconds: int = 600,
        timeout: float = 30.0,
    ) -> AgentSession:
        """Mint an Anna App Session and return a typed handle.

        - ``kind="agent"`` (default) uses the LangGraph agent loop.
        - ``agent_submode="auto"`` lets the host's auto-router pick a
          tool from the user's grant; ``"fixed"`` requires
          ``fixed_client_id``.
        - ``system_prompt`` (optional) sets a session-level system prompt
          applied to every ``run()``. The app fully controls identity /
          persona / tone; the platform only enforces a minimal,
          non-overridable safety floor. ≤ 4000 chars.
        """
        result = await self._call(
            METHOD_AGENT_SESSION_CREATE,
            {
                "kind": kind,
                "agent_submode": agent_submode if kind == "agent" else None,
                "fixed_client_id": fixed_client_id,
                "label": label,
                "quota_caps": quota_caps,
                "system_prompt": system_prompt,
                "ttl_seconds": ttl_seconds,
            },
            timeout=timeout,
        )
        granted = list(result.get("granted_tools") or [])
        sess = AgentSession(
            uuid=result["app_session_uuid"],
            expires_in=int(result.get("expires_in") or ttl_seconds),
            kind=str(result.get("kind") or kind),
            agent_submode=result.get("agent_submode"),
            fixed_client_id=result.get("fixed_client_id"),
            granted_tools=granted,
            inherit_host_tools=bool(
                result.get("inherit_host_tools", "*" in granted)
            ),
            thread_id=result.get("thread_id"),
            system_prompt=result.get("system_prompt"),
        )
        sess._client = self
        return sess

    async def attach(
        self,
        app_session_uuid: str,
        *,
        ttl_seconds: int = 600,
        timeout: float = 15.0,
    ) -> AgentSession:
        """Return a usable handle for a session that already exists.

        Works for a session this plugin created earlier (e.g. before a
        restart, found via :meth:`list`) and for one created by someone else
        inside the same App — the App's page, or another Executa it invokes.
        It refreshes first, which makes the host obtain and cache the
        session's ticket; ``run`` / ``cancel`` / ``history`` / ``delete`` on
        the returned handle then work as usual. A session this plugin may
        not access raises ``AGENT_ERR_SESSION_NOT_FOUND``.

        The refresh response does not describe the tool surface, so
        ``granted_tools`` / ``inherit_host_tools`` are only filled when the
        host reports them; read the ``run_meta`` frame of a run instead.
        """
        result = await self.refresh(
            app_session_uuid, ttl_seconds=ttl_seconds, timeout=timeout
        )
        granted = list(result.get("granted_tools") or [])
        sess = AgentSession(
            uuid=str(result.get("app_session_uuid") or app_session_uuid),
            expires_in=int(result.get("expires_in") or ttl_seconds),
            kind=str(result.get("kind") or "agent"),
            agent_submode=result.get("agent_submode"),
            fixed_client_id=result.get("fixed_client_id"),
            granted_tools=granted,
            inherit_host_tools=bool(result.get("inherit_host_tools", "*" in granted)),
            thread_id=result.get("thread_id"),
            system_prompt=result.get("system_prompt"),
        )
        sess._client = self
        return sess

    async def catalog(self, *, timeout: float = 15.0) -> dict:
        """Which platform tools a session created right now would resolve.

        The reverse-RPC twin of the page's ``anna.agent.session.catalog()``.
        Returns ``{"platform_tools": [{name, description, eligible,
        blocked_by?, ...}], "manifest_declared": [...], "declared_wildcard":
        bool}``; ``"inherit_host_tools": True`` is added when sessions would
        also inherit the conversation's tool kit. Read-only, no quota.
        """
        return await self._call(METHOD_AGENT_SESSION_CATALOG, {}, timeout=timeout)

    async def list(
        self,
        *,
        include_expired: bool = False,
        limit: int = 50,
        timeout: float = 15.0,
    ) -> List[dict]:
        """Enumerate the active sessions this plugin can reach for the current user.

        Inside an App that is every session of that App (shared with its
        page); in the conversation it is the sessions this plugin created
        there. Authenticates with the invoke ``sampling_token`` host-side,
        so it works even after a process restart wiped the in-memory token
        cache — the canonical way to recover and clean up orphaned sessions.

        Returns a list of summary dicts::

            [{"app_session_uuid": "aps_...", "kind": "agent",
              "submode": "auto", "fixed_client_id": None, "label": "...",
              "created_at": "...", "last_active_at": "...",
              "expires_at": "..."}, ...]
        """
        result = await self._call(
            METHOD_AGENT_SESSION_LIST,
            {"include_expired": include_expired, "limit": limit},
            timeout=timeout,
        )
        return list(result.get("sessions") or [])

    async def refresh(
        self,
        app_session_uuid: str,
        *,
        ttl_seconds: int = 600,
        timeout: float = 15.0,
    ) -> dict:
        """Re-mint an existing session's token and slide its idle window.

        Identity/executa-scoped (NOT dependent on a cached token): the host
        authenticates with the invoke ``sampling_token``, so this recovers a
        session by ``app_session_uuid`` even after a process restart or long
        idle wiped the in-memory token cache — the companion to :meth:`list`
        for the "resume after losing handles" flow.

        Returns the fresh lifecycle metadata::

            {"app_session_uuid": "aps_...", "expires_in": 600,
             "kind": "agent", "agent_submode": "auto",
             "fixed_client_id": None, "expires_at": "...",
             "max_lifetime_at": "...", "idle_ttl_seconds": 1800,
             "session_expires_in": 1800}

        (The underlying ``app_session_token`` is held host-side and is
        deliberately never returned to the plugin.)
        """
        return await self._call(
            METHOD_AGENT_SESSION_REFRESH,
            {"app_session_uuid": app_session_uuid, "ttl_seconds": ttl_seconds},
            timeout=timeout,
        )

    async def run_status(
        self,
        app_session_uuid: str,
        run_id: str,
        *,
        timeout: float = 15.0,
    ) -> dict:
        """Point-in-time status of one run (client-level form of
        :meth:`AgentSession.run_status` — works from a bare uuid after a
        process restart). Returns ``{status, run_id, task_id, stream_id,
        started_at?, finished_at?, model?, error?}``.
        """
        return await self._call(
            METHOD_AGENT_SESSION_RUN_STATUS,
            {"app_session_uuid": app_session_uuid, "run_id": run_id},
            timeout=timeout,
        )

    async def frames(
        self,
        stream_id: str,
        *,
        after_seq: int = 0,
        limit: int = 500,
        timeout: float = 30.0,
    ) -> dict:
        """Replay a run's archived stream frames by ``seq``.

        ``stream_id`` must come from :meth:`run_status` (the id echoed by
        ``run()`` is host-local). Page with ``after_seq`` = last seen seq
        until a frame carries ``done: true``. Rate limit 30/min/user shared
        with the platform's other pull channels — back off on
        ``AGENT_ERR_RATE_LIMITED``. Archive window ~1h.

        Returns ``{"frames": [{seq>=1, payload, done, ...}], "complete"}``.
        """
        return await self._call(
            METHOD_AGENT_SESSION_FRAMES,
            {"stream_id": stream_id, "after_seq": after_seq, "limit": limit},
            timeout=timeout,
        )

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
    "METHOD_AGENT_SESSION_LIST",
    "METHOD_AGENT_SESSION_REFRESH",
    "METHOD_AGENT_SESSION_RUN_STATUS",
    "METHOD_AGENT_SESSION_FRAMES",
    "METHOD_AGENT_SESSION_CATALOG",
    "METHOD_AGENT_SESSION_FRAME",
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
