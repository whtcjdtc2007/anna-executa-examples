"""Plugin → host progress notifications for long-running tool jobs.

Design: matrix-nexus docs/design/anna-app-tools-invoke-async-jobs.md §4.2 /
Phase E. When an app invokes your tool via ``anna.tools.invokeAsync``, the
host runs it as a long job and relays progress to the app UI in near real
time. Call :func:`emit_progress` from inside your tool handler to publish a
step update::

    from executa_sdk import bind_invoke, emit_progress

    async def handle_invoke(req_id, params):
        with bind_invoke(params):          # correlates the notification
            for i in range(total):
                do_step(i)
                emit_progress("tool_update", {"step": i + 1, "total": total,
                                              "note": f"step {i + 1} done"})

Wire shape — a JSON-RPC **notification** (no ``id``; the host never
responds)::

    {"jsonrpc": "2.0", "method": "executa/progress",
     "params": {"type": "tool_update", "data": {...},
                "context": {"invoke_id": "<parent invoke>"}}}

Semantics (host side; ignore-and-drop is silent by design):

* ``type`` must be ``"progress"`` or ``"tool_update"`` — anything else is
  coerced to ``"progress"`` (you cannot fake terminal states).
* The notification must carry the PARENT invoke's ``invoke_id`` in
  ``params.context`` (auto-stamped when inside :func:`bind_invoke`);
  unknown / finished invoke ids are dropped.
* Rate limit: 50 events/second per invoke — excess is dropped.
* Only ASYNC job invokes (``tools.invokeAsync``) have a progress channel;
  during a plain synchronous ``tools.invoke`` the events are dropped.
* ``data`` should stay small (host stores ≤8KB per event).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping, Optional

from .context import get_current_invoke_id

METHOD_EXECUTA_PROGRESS = "executa/progress"

PROGRESS_TYPES = ("progress", "tool_update")


def emit_progress(
    type_: str = "progress",
    data: Optional[Mapping[str, Any]] = None,
    *,
    invoke_id: Optional[str] = None,
    stdout=None,
) -> bool:
    """Publish one progress event for the current (or given) invoke.

    Args:
        type_: ``"progress"`` or ``"tool_update"``.
        data: Small JSON-serialisable payload (e.g. ``{"step": 3,
            "total": 10, "note": "rendering page 3"}``).
        invoke_id: Parent invoke id. Defaults to the id bound via
            :func:`executa_sdk.bind_invoke`; if neither is available the
            event cannot be correlated and is NOT sent.
        stdout: Test seam; defaults to ``sys.stdout``.

    Returns:
        True if the notification was written; False if skipped (no
        invoke_id available or serialization failed). Never raises —
        progress is best-effort and must not break the tool itself.
    """
    resolved = invoke_id or get_current_invoke_id()
    if not resolved:
        return False
    if type_ not in PROGRESS_TYPES:
        type_ = "progress"
    frame = {
        "jsonrpc": "2.0",
        "method": METHOD_EXECUTA_PROGRESS,
        "params": {
            "type": type_,
            "data": dict(data or {}),
            "context": {"invoke_id": resolved},
        },
    }
    out = stdout if stdout is not None else sys.stdout
    try:
        out.write(json.dumps(frame, ensure_ascii=False) + "\n")
        out.flush()
        return True
    except Exception:
        return False


__all__ = ["emit_progress", "METHOD_EXECUTA_PROGRESS", "PROGRESS_TYPES"]
