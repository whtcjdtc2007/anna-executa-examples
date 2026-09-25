#!/usr/bin/env python3
"""display_blocks_demo.py — Executa plugin demonstrating verbatim `_display` blocks.

Why this exists
---------------
Tool results are normally handed to the LLM as a ToolMessage and *re-synthesized*
into prose — wording, ordering, and even whole fields are probabilistic. If your
app's value depends on a specific sentence being printed exactly (e.g. "this is
the 6th time you hit this error"), prompt instructions like "print verbatim"
are not a guarantee.

The `_display` channel fixes this: blocks declared under the reserved top-level
`_display` key in a tool result are rendered **directly by the Anna host UI**,
byte-for-byte, anchored at the tool call — the model never gets a chance to
paraphrase them. The model instead receives a short note telling it the blocks
were already shown, so it composes its reply *around* them.

Contract (see matrix-nexus/docs/design/executa-display-blocks.md):

    {
      ...normal structured data for the model...,
      "_display": {
        "blocks": [
          {"type": "markdown", "text": "..."}   # 1..8 blocks
        ]
      }
    }

Limits: max 8 blocks, 4000 chars per block, 16000 chars total. Anything over
the limits (or an unknown block type) causes the whole `_display` to be
dropped with a `_display_rejected` reason — never silently truncated.

Omit-if-null falls out naturally: if a block shouldn't appear (here:
`follow_up` before the 3rd occurrence), just don't put it in the array.

This demo is a miniature "error journal": paste the same error repeatedly and
the headline counts occurrences. The headline and follow-up are computed HERE,
in code, and delivered verbatim via `_display`; the structured diagnosis stays
in the normal result for the model to reason about.

Run locally with the anna-app-cli harness:

    pnpm anna-app dev

Note: the occurrence journal is in-memory — counts reset when the plugin
process restarts. A real app would use Anna Persistent Storage
(see ../storage-notebook).
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone

# ─── Manifest ─────────────────────────────────────────────────────────

MANIFEST = {
    "display_name": "Display Blocks Demo",
    "version": "0.1.0",
    "description": (
        "Demonstrates the `_display` verbatim render channel: an error journal "
        "whose headline ('Nth time you hit this') and follow-up are rendered "
        "byte-for-byte by the host UI instead of being paraphrased by the model."
    ),
    "author": "Anna Developer",
    "tools": [
        {
            "name": "diagnose_error",
            "description": (
                "Diagnose a pasted error message and check the per-session "
                "incident journal for prior occurrences. Presentation-critical "
                "lines (headline, follow-up) are returned as verbatim display "
                "blocks; the structured diagnosis is for you to reason about."
            ),
            "parameters": [
                {
                    "name": "error_text",
                    "type": "string",
                    "description": "The raw error message / traceback, verbatim.",
                    "required": True,
                },
            ],
        },
        {
            "name": "journal_stats",
            "description": "Show how many distinct errors this session's journal has seen.",
            "parameters": [],
        },
    ],
    "runtime": {
        "type": "uv",
        "min_version": "0.1.0",
    },
}

# ─── In-memory incident journal ──────────────────────────────────────
# fingerprint -> {"count": int, "first_seen": iso8601}
_JOURNAL: dict[str, dict] = {}

# Follow-up only appears from the 3rd occurrence — demonstrating that
# conditional display is plain code, not a prompt instruction.
FOLLOW_UP_THRESHOLD = 3
_FOLLOW_UP_TEXT = (
    "_Tell me if this fixes it and it goes in your logbook for next time._"
)


def _fingerprint(error_text: str) -> str:
    """Stable fingerprint: collapse whitespace/line numbers so re-pastes match."""
    normalized = re.sub(r"line \d+", "line N", error_text)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _classify(error_text: str) -> tuple[str, list[str], str]:
    """Toy knowledge base: category, fix steps, verify command."""
    lowered = error_text.lower()
    if "modulenotfounderror" in lowered or "no module named" in lowered:
        m = re.search(r"no module named ['\"]?([\w.]+)", lowered)
        mod = m.group(1) if m else "the-module"
        return (
            "python.module_not_found_error",
            [
                f"Confirm which interpreter is active: `python -c 'import sys; print(sys.executable)'`",
                f"Install the package into that interpreter: `pip install {mod}`",
                "Re-run the failing command in the same shell",
            ],
            f"python -c 'import {mod}'",
        )
    if "permission denied" in lowered:
        return (
            "os.permission_denied",
            [
                "Check ownership of the target path: `ls -l <path>`",
                "Fix permissions (`chmod`/`chown`) rather than reaching for sudo",
            ],
            "ls -l <path>",
        )
    return (
        "unclassified",
        ["No curated fix in the demo knowledge base — inspect the traceback."],
        "",
    )


# ─── Tool implementations ────────────────────────────────────────────


def tool_diagnose_error(error_text: str) -> dict:
    fp = _fingerprint(error_text)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = _JOURNAL.setdefault(fp, {"count": 0, "first_seen": now})
    entry["count"] += 1
    count = entry["count"]

    category, fix_steps, verify_command = _classify(error_text)

    # ── Presentation-critical lines, computed server-side ──
    if count == 1:
        headline = (
            f"📒 **First time in your journal** — fingerprint `{fp}`, "
            f"recorded {now}."
        )
    else:
        headline = (
            f"📒 **This is the {_ordinal(count)} time you have hit this** — "
            f"first seen {entry['first_seen']} (fingerprint `{fp}`)."
        )

    blocks = [{"type": "markdown", "text": headline}]
    if count >= FOLLOW_UP_THRESHOLD:
        # Omit-if-null in action: the block simply isn't emitted before
        # the threshold — no prompt logic involved.
        blocks.append({"type": "markdown", "text": _FOLLOW_UP_TEXT})

    return {
        # ── Structured data: the model reasons about / narrates this ──
        "fingerprint": fp,
        "category": category,
        "occurrence_count": count,
        "first_seen": entry["first_seen"],
        "fix_steps": fix_steps,
        "verify_command": verify_command,
        # ── Verbatim channel: the host renders this, byte-for-byte ──
        "_display": {"blocks": blocks},
    }


def tool_journal_stats() -> dict:
    total = sum(e["count"] for e in _JOURNAL.values())
    return {
        "distinct_errors": len(_JOURNAL),
        "total_occurrences": total,
        "_display": {
            "blocks": [
                {
                    "type": "markdown",
                    "text": (
                        f"📒 **Journal**: {len(_JOURNAL)} distinct error(s), "
                        f"{total} total occurrence(s) this session."
                    ),
                }
            ]
        },
    }


TOOL_DISPATCH = {
    "diagnose_error": tool_diagnose_error,
    "journal_stats": tool_journal_stats,
}


# ─── JSON-RPC over stdio ─────────────────────────────────────────────


def make_response(id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def handle_request(line: str) -> str:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return json.dumps(
            make_response(None, error={"code": -32700, "message": "Parse error"})
        )

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if method == "describe":
        # describe result MUST be the manifest itself (not wrapped)
        response = make_response(request_id, result=MANIFEST)
    elif method == "invoke":
        tool_name = params.get("tool")
        arguments = params.get("arguments", {})
        fn = TOOL_DISPATCH.get(tool_name)
        if fn is None:
            response = make_response(
                request_id,
                error={
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}",
                    "data": {"available_tools": list(TOOL_DISPATCH.keys())},
                },
            )
        else:
            try:
                result = fn(**arguments)
                response = make_response(
                    request_id,
                    result={"success": True, "data": result, "tool": tool_name},
                )
            except TypeError as e:
                response = make_response(
                    request_id,
                    error={"code": -32602, "message": f"Invalid parameters: {e}"},
                )
            except Exception as e:
                response = make_response(
                    request_id,
                    error={"code": -32603, "message": f"Tool execution failed: {e}"},
                )
    elif method == "health":
        response = make_response(
            request_id,
            result={
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": MANIFEST["version"],
                "tools_count": len(MANIFEST["tools"]),
            },
        )
    else:
        response = make_response(
            request_id,
            error={"code": -32601, "message": f"Method not found: {method}"},
        )

    return json.dumps(response, ensure_ascii=False)


def main():
    print("📒 display-blocks-demo plugin started", file=sys.stderr, flush=True)
    # Long-running: loop until the Agent closes stdin.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(handle_request(line), flush=True)


if __name__ == "__main__":
    main()
