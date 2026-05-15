#!/usr/bin/env python3
"""
focus-session — Executa stdio tool plugin (single-dispatcher method)

Persists Pomodoro / deep-work sessions to ``~/.anna/focus-flow/state.json``
and exposes ONE tool method (``session``) that takes an ``action`` discriminator.

Why one method instead of five?
    Anna's UI Runtime allocates one Executa row per running plugin (matched
    by the server-minted ``tool_id``). Inside that plugin, individual tools
    are addressed via the ``method`` arg on ``anna.tools.invoke``. Keeping
    the plugin to a single dispatcher tool means the bundle just toggles
    ``action`` instead of registering a new Executa per behavior::

        Plugin MANIFEST.name           = "<minted tool_id>"  (e.g. tool-yourhandle-focus-session-abcd1234)
        Plugin MANIFEST.tools[0].name  = "session"
        Executa row tool_id            = "<minted tool_id>"  (same string; minted on https://anna.partners/executa)
        Bundle call                    = anna.tools.invoke({
                                            tool_id: "<minted tool_id>",
                                            method:  "session",
                                            args:    { action: "start", ... },
                                          })

Protocol: JSON-RPC 2.0 over stdio
Methods:  describe, invoke, health
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Plugin manifest — Anna calls ``describe`` and uses this dict verbatim.
# ---------------------------------------------------------------------------
MANIFEST: dict[str, Any] = {
    # MUST equal the minted tool_id from https://anna.partners/executa.
    # Use ``scripts/set-tool-id.py apply --tool <minted>`` to fill this in
    # together with pyproject.toml, manifest.json, and bundle/app.js.
    "name": "tool-test-focus-session-12345678",
    "display_name": "Focus Session",
    "version": "1.0.0",
    "description": (
        "Pomodoro / deep-work session timer. State persists to "
        "~/.anna/focus-flow/state.json."
    ),
    "author": "Acme Labs",
    "homepage": "https://github.com/openclaw/anna-executa-examples",
    "license": "MIT",
    "tags": ["productivity", "focus", "pomodoro", "anna-app"],
    "tools": [
        {
            "name": "session",
            "description": (
                "Manage a focus session. Use the `action` parameter to select "
                "an operation: start | pause | resume | complete | get_state."
            ),
            "parameters": [
                {
                    "name": "action",
                    "type": "string",
                    "description": "One of: start, pause, resume, complete, get_state.",
                    "required": True,
                },
                {
                    "name": "duration_minutes",
                    "type": "integer",
                    "description": "Required when action='start'. 1-180 minutes.",
                    "required": False,
                },
                {
                    "name": "topic",
                    "type": "string",
                    "description": "Optional label for action='start' (max 120 chars).",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "notes",
                    "type": "string",
                    "description": "Optional reflection for action='complete' (max 500 chars).",
                    "required": False,
                    "default": "",
                },
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
STATE_DIR = Path(os.path.expanduser("~/.anna/focus-flow"))
STATE_FILE = STATE_DIR / "state.json"
MAX_HISTORY = 200


def _now() -> float:
    return time.time()


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"active": None, "history": []}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("state.json root must be an object")
        data.setdefault("active", None)
        data.setdefault("history", [])
        return data
    except (json.JSONDecodeError, ValueError) as e:
        backup = STATE_FILE.with_suffix(f".broken.{int(_now())}.json")
        try:
            STATE_FILE.rename(backup)
            print(
                f"[focus-session] corrupt state moved to {backup}: {e}",
                file=sys.stderr,
            )
        except OSError:
            pass
        return {"active": None, "history": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def _today_totals(history: list[dict[str, Any]]) -> dict[str, Any]:
    today_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    today = [h for h in history if h.get("completed_at", 0) >= today_start]
    seconds = sum(int(h.get("focused_seconds", 0)) for h in today)
    return {
        "session_count": len(today),
        "focused_minutes": round(seconds / 60, 1),
        "focused_seconds": seconds,
    }


def _focused_seconds(active: dict[str, Any]) -> int:
    if not active:
        return 0
    accumulated = int(active.get("accumulated_seconds", 0))
    if active.get("status") == "running":
        accumulated += int(_now() - active.get("running_since", _now()))
    return max(0, accumulated)


def _active_view(active: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of ``active`` enriched with derived fields the bundle UI
    relies on (focused_seconds / remaining_seconds). Centralising this here
    means every action returns the same shape, so the iframe never has to wait
    for the next ``get_state`` poll just to learn the timer values.
    """
    if not active:
        return None
    view = dict(active)
    focused = _focused_seconds(active)
    view["focused_seconds"] = focused
    view["remaining_seconds"] = max(
        0, int(active.get("duration_seconds", 0)) - focused
    )
    return view


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------

def _action_start(duration_minutes: int | None, topic: str) -> dict[str, Any]:
    if duration_minutes is None:
        raise ValueError("duration_minutes is required for action='start'")
    duration_minutes = int(duration_minutes)
    if not 1 <= duration_minutes <= 180:
        raise ValueError("duration_minutes must be between 1 and 180")
    topic = (topic or "").strip()[:120]
    state = _load_state()
    now = _now()
    state["active"] = {
        "id": uuid.uuid4().hex,
        "topic": topic,
        "duration_seconds": duration_minutes * 60,
        "started_at": now,
        "running_since": now,
        "accumulated_seconds": 0,
        "status": "running",
    }
    _save_state(state)
    return {"active": _active_view(state["active"])}


def _action_pause() -> dict[str, Any]:
    state = _load_state()
    active = state.get("active")
    if not active:
        return {"active": None, "message": "No active session to pause."}
    if active.get("status") == "running":
        active["accumulated_seconds"] = _focused_seconds(active)
        active["status"] = "paused"
        active["running_since"] = None
        _save_state(state)
    return {"active": _active_view(active)}


def _action_resume() -> dict[str, Any]:
    state = _load_state()
    active = state.get("active")
    if not active:
        return {"active": None, "message": "No active session to resume."}
    if active.get("status") != "running":
        active["status"] = "running"
        active["running_since"] = _now()
        _save_state(state)
    return {"active": _active_view(active)}


def _action_complete(notes: str) -> dict[str, Any]:
    state = _load_state()
    active = state.get("active")
    if not active:
        return {"completed": None, "message": "No active session."}
    focused = _focused_seconds(active)
    record = {
        "id": active["id"],
        "topic": active.get("topic", ""),
        "duration_seconds": active.get("duration_seconds", 0),
        "focused_seconds": focused,
        "started_at": active.get("started_at"),
        "completed_at": _now(),
        "notes": (notes or "").strip()[:500],
    }
    history = state.get("history", [])
    history.insert(0, record)
    state["history"] = history[:MAX_HISTORY]
    state["active"] = None
    _save_state(state)
    return {"completed": record, "today": _today_totals(state["history"])}


def _action_get_state() -> dict[str, Any]:
    state = _load_state()
    history = state.get("history", [])[:10]
    return {
        "active": _active_view(state.get("active")),
        "today": _today_totals(state.get("history", [])),
        "recent": history,
    }


def tool_session(
    action: str,
    duration_minutes: int | None = None,
    topic: str = "",
    notes: str = "",
) -> dict[str, Any]:
    if action == "start":
        return _action_start(duration_minutes, topic)
    if action == "pause":
        return _action_pause()
    if action == "resume":
        return _action_resume()
    if action == "complete":
        return _action_complete(notes)
    if action == "get_state":
        return _action_get_state()
    raise ValueError(
        f"unknown action: {action!r}; expected one of "
        "start | pause | resume | complete | get_state"
    )


TOOL_DISPATCH = {"session": tool_session}


# ---------------------------------------------------------------------------
# JSON-RPC handlers
# ---------------------------------------------------------------------------

def handle_describe(_params: dict[str, Any]) -> dict[str, Any]:
    return MANIFEST


def handle_invoke(params: dict[str, Any]) -> Any:
    tool_name = params.get("tool")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise ValueError("`arguments` must be an object")
    fn = TOOL_DISPATCH.get(tool_name)
    if fn is None:
        raise ValueError(f"unknown tool: {tool_name!r}")
    # The Executa runtime expects an `InvokeResult` shape:
    #   {"success": true, "data": <tool payload>}
    # If we return the raw payload directly, `InvokeResult.from_dict` reads
    # missing `success` as False and the host treats the call as a failure.
    try:
        payload = fn(**args)
    except Exception as exc:  # surface tool errors via InvokeResult
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"success": True, "data": payload}


def handle_health(_params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "state_file": str(STATE_FILE)}


METHOD_DISPATCH = {
    "describe": handle_describe,
    "invoke": handle_invoke,
    "health": handle_health,
}


# ---------------------------------------------------------------------------
# Stdio loop
# ---------------------------------------------------------------------------

def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    print(
        f"[focus-session] {MANIFEST['display_name']} v{MANIFEST['version']} ready",
        file=sys.stderr,
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {e}"},
                }
            )
            continue

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        handler = METHOD_DISPATCH.get(method)
        if handler is None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )
            continue
        try:
            result = handler(params)
            send({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:  # noqa: BLE001
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )


if __name__ == "__main__":
    main()
