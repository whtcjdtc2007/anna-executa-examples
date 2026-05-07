"""Anna Executa Python SDK — Sampling support

`SamplingClient` lets a long-running Executa plugin issue a reverse
JSON-RPC `sampling/createMessage` request to its host Agent, asking the
host to perform an LLM completion on the plugin's behalf.

Why reverse RPC?
- Plugins do NOT need their own LLM API key — billing/quotas/model
  routing are owned by the host (Anna).
- Plugins can describe a desired model via `modelPreferences` (MCP
  convention) and let the host pick a concrete model based on the user's
  saved preferences.

Wire protocol (Executa v2):
    Plugin (us)                                 Agent (host)
    ────────────────────────────────────────────────────────────────
    invoke(req_id=42, …)              ──►   (host called us)
    sampling/createMessage(req_id=A)  ──►   (we ask host to sample)
    ◄── result | error                      (host replies)
    invoke result(req_id=42)          ──►   (we finish original tool)

Threading model:
- The plugin's stdin reader loop receives BOTH agent-initiated requests
  AND responses to plugin-initiated requests. Use the `dispatch_message`
  helper to fan-out by frame shape.
- A single :class:`SamplingClient` instance per process is enough; it
  multiplexes outstanding reverse RPCs by `id`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


# ─── Constants — keep in sync with matrix/src/executa/protocol.py ─────

PROTOCOL_VERSION_V1 = "1.1"
PROTOCOL_VERSION_V2 = "2.0"
MAX_STDIO_MESSAGE_BYTES = 512 * 1024

METHOD_INITIALIZE = "initialize"
METHOD_SHUTDOWN = "shutdown"
METHOD_SAMPLING_CREATE_MESSAGE = "sampling/createMessage"

# Sampling error codes
SAMPLING_ERR_NOT_GRANTED = -32001
SAMPLING_ERR_QUOTA_EXCEEDED = -32002
SAMPLING_ERR_PROVIDER_ERROR = -32003
SAMPLING_ERR_INVALID_REQUEST = -32004
SAMPLING_ERR_TIMEOUT = -32005
SAMPLING_ERR_MAX_CALLS_EXCEEDED = -32006
SAMPLING_ERR_MAX_TOKENS_EXCEEDED = -32007
SAMPLING_ERR_NOT_NEGOTIATED = -32008
SAMPLING_ERR_USER_DENIED = -32009


class SamplingError(Exception):
    """Wraps a JSON-RPC error returned by the host for `sampling/createMessage`."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


# ─── Frame I/O ────────────────────────────────────────────────────────


def _write_frame(msg: dict, *, stdout=None) -> None:
    """Write one JSON-RPC frame to stdout (or `stdout` arg). Thread-safe."""
    if stdout is None:
        stdout = sys.stdout
    payload = json.dumps(msg, ensure_ascii=False)
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > MAX_STDIO_MESSAGE_BYTES:
        # File transport — only valid for plugin → host responses, not for
        # plugin-initiated reverse RPC requests. We still support it here
        # for symmetry.
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="executa-msg-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            os.close(fd)
            raise
        pointer = json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"), "__file_transport": tmp}
        )
        stdout.write(pointer + "\n")
    else:
        stdout.write(payload + "\n")
    stdout.flush()


# ─── SamplingClient ───────────────────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class SamplingClient:
    """Issue reverse `sampling/createMessage` requests to the host.

    Async / threading model:
    - Construct one instance per process; share across tools.
    - The plugin's stdin loop must call :meth:`dispatch_response` on every
      JSON message that has no `method` field (i.e. it's a response to
      something WE asked the host).
    - All public methods are async; results are returned as plain dicts
      shaped like the MCP `sampling/createMessage` result.
    """

    def __init__(self, *, write_frame: Callable[[dict], None] | None = None):
        self._write_frame = write_frame or _write_frame
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Will be set lazily when the first request is awaited so we can
        # resolve futures from threads (e.g. stdin reader thread).
        self._sampling_disabled_reason: Optional[str] = None

    # — public API —

    async def create_message(
        self,
        *,
        messages: List[dict],
        max_tokens: int,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
        model_preferences: Optional[dict] = None,
        include_context: str = "none",
        metadata: Optional[dict] = None,
        timeout: float = 90.0,
    ) -> dict:
        """Ask the host to run an LLM completion. Returns the host result dict.

        Args:
            messages:          MCP-shaped messages, e.g.
                ``[{"role":"user","content":{"type":"text","text":"..."}}]``
            max_tokens:        Required; per-call cap (host enforces hard upper bound).
            system_prompt:     Optional system message.
            temperature:       Optional sampling temperature.
            stop_sequences:    Optional stop strings.
            model_preferences: MCP-style ``{"hints":[{"name":"..."}],
                                "costPriority":0..1, "speedPriority":..., "intelligencePriority":...}``.
                Omit (None) to let the host fall back to the user's saved
                ``preferred_model``.
            include_context:   Phase 1 only supports ``"none"``.
            metadata:          Arbitrary string→string map for audit/tracing.
            timeout:           Wall-clock seconds before raising ``asyncio.TimeoutError``.
        """
        if self._sampling_disabled_reason:
            raise SamplingError(
                SAMPLING_ERR_NOT_NEGOTIATED, self._sampling_disabled_reason
            )

        if not messages:
            raise ValueError("messages must be a non-empty list")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")

        loop = asyncio.get_running_loop()
        self._loop = loop
        req_id = uuid.uuid4().hex

        params: Dict[str, Any] = {
            "messages": messages,
            "maxTokens": max_tokens,
            "includeContext": include_context,
        }
        if system_prompt is not None:
            params["systemPrompt"] = system_prompt
        if temperature is not None:
            params["temperature"] = temperature
        if stop_sequences:
            params["stopSequences"] = stop_sequences
        if model_preferences:
            params["modelPreferences"] = model_preferences
        if metadata:
            params["metadata"] = metadata

        future: asyncio.Future[dict] = loop.create_future()
        with self._lock:
            self._pending[req_id] = _Pending(future=future)

        envelope = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": METHOD_SAMPLING_CREATE_MESSAGE,
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
            raise SamplingError(
                SAMPLING_ERR_TIMEOUT,
                f"sampling/createMessage timed out after {timeout}s",
            )

    # — wiring —

    def disable(self, reason: str) -> None:
        """Mark sampling as unavailable (e.g. host did not negotiate v2)."""
        self._sampling_disabled_reason = reason

    def is_response_envelope(self, msg: dict) -> bool:
        """True if `msg` looks like a reply to one of our reverse RPCs."""
        if not isinstance(msg, dict):
            return False
        if "method" in msg:
            return False
        return "id" in msg and msg.get("id") in self._pending

    def dispatch_response(self, msg: dict) -> bool:
        """Resolve the matching pending future. Returns True if handled."""
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
                    SamplingError(
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
            # Loop is closed — fall back to direct (best-effort)
            _resolve()
        return True


__all__ = [
    "SamplingClient",
    "SamplingError",
    "PROTOCOL_VERSION_V1",
    "PROTOCOL_VERSION_V2",
    "MAX_STDIO_MESSAGE_BYTES",
    "METHOD_INITIALIZE",
    "METHOD_SAMPLING_CREATE_MESSAGE",
    "SAMPLING_ERR_NOT_GRANTED",
    "SAMPLING_ERR_QUOTA_EXCEEDED",
    "SAMPLING_ERR_PROVIDER_ERROR",
    "SAMPLING_ERR_INVALID_REQUEST",
    "SAMPLING_ERR_TIMEOUT",
    "SAMPLING_ERR_MAX_CALLS_EXCEEDED",
    "SAMPLING_ERR_MAX_TOKENS_EXCEEDED",
    "SAMPLING_ERR_NOT_NEGOTIATED",
    "SAMPLING_ERR_USER_DENIED",
]
