#!/usr/bin/env python3
"""long_task_engine_plugin.py — Executa that demonstrates the async tool-job
channel (`anna.tools.invokeAsync`, design anna-app-tools-invoke-async-jobs.md).

One tool:

    run_steps(steps, step_seconds, fail_at_step?)
        Sleeps `step_seconds` per step for `steps` steps, emitting an
        `executa/progress` notification after each step:

            emit_progress("tool_update", {"step": i, "total": steps,
                                          "note": f"step {i}/{steps} done"})

        Returns {"ok": True, "steps": steps, "elapsed_s": ...} on success.

The important bits to copy into your own long-running Executa:

1. **`bind_invoke(params)`** at the top of the handler — stamps the parent
   `invoke_id` onto every `emit_progress` notification (and onto any
   reverse-RPC you make). Without it the host cannot correlate the event
   and silently drops it.
2. **`emit_progress` is best-effort** — rate-limited host-side at
   50 events/s per invoke; excess is dropped without error. Keep `data`
   small (host stores ≤8KB/event, keeps the latest 500).
3. Progress only flows for ASYNC invokes (`anna.tools.invokeAsync`).
   During a plain sync `tools.invoke` the notifications are dropped —
   the tool still works, you just get no live progress.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Fallback for fresh checkouts: locate the in-repo SDK when not pip-installed.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[4] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import bind_invoke, emit_progress  # noqa: E402

# ─── Manifest ─────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Long Task Engine",
    "version": "0.1.0",
    "description": (
        "Demo Executa for the anna.tools.invokeAsync job channel: a "
        "configurable multi-step long task that reports live progress "
        "via executa/progress notifications."
    ),
    "author": "Anna Developer",
    # No host_capabilities needed — progress notifications are part of the
    # base invoke channel, not a granted capability.
    "tools": [
        {
            "name": "run_steps",
            "description": (
                "Run a simulated long task of `steps` steps, sleeping "
                "`step_seconds` per step and emitting a progress event "
                "after each. Use with anna.tools.invokeAsync for live "
                "progress; a plain tools.invoke also works (no progress, "
                "and it times out past the 90s sync ceiling — that "
                "contrast is the point of the demo)."
            ),
            # Executa protocol uses `parameters: [{name, type, ...}]`
            # (docs/protocol-spec.md), NOT MCP-style `input_schema`.
            "parameters": [
                {
                    "name": "steps",
                    "type": "integer",
                    "description": "Number of steps (1-1000).",
                    "required": True,
                },
                {
                    "name": "step_seconds",
                    "type": "number",
                    "description": "Seconds to sleep per step (0-60).",
                    "required": True,
                },
                {
                    "name": "fail_at_step",
                    "type": "integer",
                    "description": (
                        "Optional: raise an error when reaching this step "
                        "(demonstrates the failed terminal state)."
                    ),
                    "required": False,
                },
            ],
            # Long tool: generous per-tool default; the effective deadline
            # is set per-call by the host (invoke params.timeout).
            "timeout": 86_400,
        }
    ],
}

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


def _ok(req_id: Any, result: dict) -> None:
    _write_frame({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id: Any, code: int, message: str, data: dict | None = None) -> None:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    _write_frame({"jsonrpc": "2.0", "id": req_id, "error": err})


# ─── Tool implementation ──────────────────────────────────────────────


def _run_steps(steps: int, step_seconds: float, fail_at_step: int | None) -> dict:
    t0 = time.monotonic()
    for i in range(1, steps + 1):
        time.sleep(step_seconds)
        if fail_at_step is not None and i >= fail_at_step:
            raise RuntimeError(f"simulated failure at step {i}/{steps}")
        # Live progress — correlated to the parent invoke by bind_invoke.
        # Locking: emit_progress writes a full line + flush; our
        # _stdout_lock is not needed because the SDK writes atomically
        # per line (single .write call under CPython's GIL); for belt
        # and braces we route it through the shared lock anyway.
        with _stdout_lock:
            emit_progress(
                "tool_update",
                {"step": i, "total": steps, "note": f"step {i}/{steps} done"},
            )
    return {
        "ok": True,
        "steps": steps,
        "elapsed_s": round(time.monotonic() - t0, 3),
    }


def _handle_invoke(req_id: Any, params: dict) -> None:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    if tool != "run_steps":
        _err(req_id, -32601, f"Unknown tool: {tool}")
        return
    try:
        steps = max(1, min(1000, int(args.get("steps", 1))))
        step_seconds = max(0.0, min(60.0, float(args.get("step_seconds", 1))))
        fail_raw = args.get("fail_at_step")
        fail_at_step = int(fail_raw) if fail_raw is not None else None
    except (TypeError, ValueError) as e:
        _err(req_id, -32602, f"Invalid arguments: {e}")
        return

    # bind_invoke stamps context.invoke_id on every emit_progress below.
    with bind_invoke(params):
        try:
            data = _run_steps(steps, step_seconds, fail_at_step)
        except Exception as e:  # noqa: BLE001
            _err(req_id, -32603, f"Tool execution failed: {e}")
            return
    # InvokeResult.from_dict on the host expects {success, data}; never
    # return the bare tool dict (it would be read as success=False).
    _ok(req_id, {"success": True, "tool": tool, "data": data})


def _handle_initialize(req_id: Any, params: dict) -> None:
    proto = (params or {}).get("protocolVersion") or "2.0"
    _ok(
        req_id,
        {
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {
                "name": MANIFEST["display_name"],
                "version": MANIFEST["version"],
            },
            "capabilities": {},
        },
    )


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    if "method" not in msg:
        return  # no reverse-RPC clients in this plugin — nothing pending

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        _handle_initialize(req_id, params)
    elif method == "describe":
        # `result` MUST be the bare manifest — matrix's ToolManifest.from_dict
        # reads its fields directly.
        _ok(req_id, MANIFEST)
    elif method == "health":
        _ok(req_id, {"status": "healthy", "version": MANIFEST["version"]})
    elif method == "invoke":
        _handle_invoke(req_id, params)
    elif method == "shutdown":
        _ok(req_id, {"ok": True})
    elif req_id is not None:
        _err(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    print("🔌 long-task-engine plugin started", file=sys.stderr)
    # Each invoke runs on its own thread so a long-running run_steps does
    # not block health checks or concurrent invokes.
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="invoke")
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            pool.submit(_handle_message, line)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
