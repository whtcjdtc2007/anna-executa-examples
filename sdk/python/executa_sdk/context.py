"""Invoke context — gives plugin tools a typed view of the per-call
``params.context`` payload sent by the host Agent.

Example
-------

.. code-block:: python

    from executa_sdk import InvokeContext

    async def handle_invoke(request):
        ctx = InvokeContext.from_params(request["params"])
        if ctx.remaining_s() <= 0:
            return error("subcall_timeout", "no time left in budget")
        # Optionally tighten the next reverse-RPC. The host loader will
        # auto-inject ``_clientTimeoutS`` from ``ctx.deadline_ms`` if you
        # don't, but explicit is friendlier when the plugin wants a
        # shorter slice than "all remaining time".
        await storage.set(key, value, timeout=min(5.0, ctx.remaining_s()))

The host now propagates ``deadline_ms`` (a Unix epoch milliseconds
absolute deadline derived from the invoke ``timeoutMs``) into
``params.context.deadline_ms``. Older hosts will simply omit it, in
which case :meth:`remaining_s` returns :data:`math.inf`.
"""
from __future__ import annotations

import contextlib
import math
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional


@dataclass(frozen=True)
class InvokeContext:
    """Typed view of ``params.context`` for a single tool invocation."""

    invoke_id: Optional[str] = None
    plugin_name: Optional[str] = None
    deadline_ms: Optional[int] = None
    credentials: Mapping[str, Any] | None = None
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None) -> "InvokeContext":
        """Build from the raw ``params`` dict of an ``invoke`` request."""
        if not isinstance(params, Mapping):
            return cls()
        ctx = params.get("context") if isinstance(params.get("context"), Mapping) else {}
        deadline = ctx.get("deadline_ms")
        try:
            deadline_int = int(deadline) if deadline is not None else None
        except (TypeError, ValueError):
            deadline_int = None
        return cls(
            invoke_id=ctx.get("invoke_id") or params.get("invoke_id"),
            plugin_name=ctx.get("plugin_name"),
            deadline_ms=deadline_int,
            credentials=ctx.get("credentials") if isinstance(ctx.get("credentials"), Mapping) else None,
            raw=ctx or None,
        )

    def remaining_s(self) -> float:
        """Seconds left in the invoke budget. ``math.inf`` if unknown."""
        if self.deadline_ms is None:
            return math.inf
        return max(0.0, (self.deadline_ms / 1000.0) - time.time())

    def has_deadline(self) -> bool:
        return self.deadline_ms is not None

    def expired(self) -> bool:
        """True iff a deadline is set and has already passed."""
        return self.has_deadline() and self.remaining_s() <= 0.0


# ─── Current-invoke propagation (reverse-RPC correlation) ─────────────
#
# The host Agent associates every reverse RPC (host/uploadFile,
# storage/*, image/*, sampling/createMessage, …) with its parent
# ``invoke`` via ``params.context.invoke_id``. When a plugin handles
# multiple invokes CONCURRENTLY, omitting this field forces the host to
# guess — which intermittently attributes the reverse RPC to the wrong
# invoke (forum #188: negotiate/confirm r2_key ownership failures).
#
# Bind the current invoke at the top of your tool handler and every SDK
# client automatically stamps outgoing reverse RPCs::
#
#     from executa_sdk import bind_invoke
#
#     async def handle_invoke(req_id, params):
#         with bind_invoke(params):
#             ...  # any SDK reverse-RPC call made here is correlated
#
# ``ContextVar`` scoping means concurrent asyncio tasks each see their
# own binding. NOTE: contextvars do NOT flow across raw threads — if you
# hop threads (e.g. ``run_coroutine_threadsafe``), re-bind inside the
# target coroutine.
#
# Outside any invoke. A reverse RPC issued with nothing bound carries no
# ``invoke_id``; the host serves it with the Executa's background
# credentials (process start, timers, work that continues after an invoke
# returned). An asyncio task created inside a handler INHERITS that
# handler's binding, so a task meant to outlive its invoke must detach
# explicitly::
#
#     async def long_running_work():
#         with bind_invoke(None):
#             ...  # reverse RPCs here are background, not the old invoke

_CURRENT_INVOKE_ID: ContextVar[Optional[str]] = ContextVar(
    "executa_sdk_current_invoke_id", default=None
)


def set_current_invoke_id(invoke_id: Optional[str]) -> "Token[Optional[str]]":
    """Low-level setter; prefer :func:`bind_invoke`. Returns a reset token."""
    return _CURRENT_INVOKE_ID.set(invoke_id)


def reset_current_invoke_id(token: "Token[Optional[str]]") -> None:
    _CURRENT_INVOKE_ID.reset(token)


def get_current_invoke_id() -> Optional[str]:
    """The ``invoke_id`` bound to the current task, or ``None``."""
    return _CURRENT_INVOKE_ID.get()


@contextlib.contextmanager
def bind_invoke(
    params_or_invoke_id: Mapping[str, Any] | str | None,
) -> Iterator[Optional[str]]:
    """Bind the current invoke for the duration of the ``with`` block.

    Accepts either the raw ``params`` of the ``invoke`` request (the
    ``invoke_id`` is extracted via :meth:`InvokeContext.from_params`) or
    an ``invoke_id`` string directly.
    """
    if isinstance(params_or_invoke_id, str) or params_or_invoke_id is None:
        invoke_id: Optional[str] = params_or_invoke_id or None
    else:
        invoke_id = InvokeContext.from_params(params_or_invoke_id).invoke_id
    token = _CURRENT_INVOKE_ID.set(invoke_id)
    try:
        yield invoke_id
    finally:
        _CURRENT_INVOKE_ID.reset(token)


def attach_invoke_context(params: dict) -> dict:
    """Stamp ``params.context.invoke_id`` from the current binding.

    Used by every SDK reverse-RPC client just before writing the request
    frame. No-op when nothing is bound or the caller already provided a
    ``context.invoke_id``. Mutates and returns ``params``.
    """
    invoke_id = _CURRENT_INVOKE_ID.get()
    if not invoke_id:
        return params
    ctx = params.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
        params["context"] = ctx
    ctx.setdefault("invoke_id", invoke_id)
    return params


__all__ = [
    "InvokeContext",
    "bind_invoke",
    "get_current_invoke_id",
    "set_current_invoke_id",
    "reset_current_invoke_id",
    "attach_invoke_context",
]
