#!/usr/bin/env python3
"""Executa Plugin Example — Python Implementation

A complete Executa plugin example demonstrating how to implement the standard
JSON-RPC 2.0 over stdio protocol. Anna Agent can automatically discover, load,
and invoke the tools exposed by this plugin.

Usage:
    python example_plugin.py

Install as a uv tool:
    uv tool install .

Build as a standalone binary:
    ./build_binary.sh --test

Protocol requirements:
    - stdin:  Receives JSON-RPC requests (one JSON object per line)
    - stdout: Returns JSON-RPC responses (one JSON object per line)
    - stderr: Log output (does not interfere with protocol communication)

⚠️  CRITICAL — the plugin process must be LONG-RUNNING:
    - Loop on `for line in sys.stdin:` until EOF (the Agent closes stdin to shut you down)
    - NEVER call `sys.exit()` after handling a single request
    - Always `sys.stdout.flush()` after writing a response
    A one-shot process passes `describe` once and then shows up as **Stopped**
    in the Agent UI forever, paying a fresh cold-start on every invoke.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# Size threshold (bytes) for a single stdio message; file transport is used when exceeded
MAX_STDIO_MESSAGE_BYTES = 512 * 1024


# ─── Manifest (Self-Description) ─────────────────────────────────────
#
# name:         Unique tool identifier, corresponds to tool_id in Anna Admin
# display_name: Human-readable name, corresponds to name in Anna Admin
# tools:        List of tools, each containing name, description, and parameters

MANIFEST = {
    "name": "example-text-tool",
    "display_name": "Example Text Tool",
    "version": "1.0.0",
    "description": "An example text processing tool demonstrating the full Executa plugin protocol implementation",
    "author": "Anna Developer",
    "tools": [
        {
            "name": "word_count",
            "description": "Count the number of words, characters, and lines in text",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "The text content to analyze",
                    "required": True,
                },
            ],
        },
        {
            "name": "text_transform",
            "description": "Transform text format (uppercase, lowercase, title case, reverse)",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "The text to transform",
                    "required": True,
                },
                {
                    "name": "transform",
                    "type": "string",
                    "description": "Transform type: upper / lower / title / reverse",
                    "required": True,
                },
            ],
        },
        {
            "name": "text_repeat",
            "description": "Repeat text a specified number of times with an optional separator",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "The text to repeat",
                    "required": True,
                },
                {
                    "name": "count",
                    "type": "integer",
                    "description": "Number of repetitions (1-100)",
                    "required": False,
                    "default": 2,
                },
                {
                    "name": "separator",
                    "type": "string",
                    "description": "Separator",
                    "required": False,
                    "default": " ",
                },
            ],
        },
        {
            "name": "batch_word_count",
            "description": "Batch word count for multiple texts (demonstrates array parameter usage)",
            "parameters": [
                {
                    "name": "texts",
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of texts to analyze",
                    "required": True,
                },
            ],
        },
        {
            "name": "generate_dataset",
            "description": "Generate a mock dataset (can produce large responses, demonstrates file transport mechanism)",
            "parameters": [
                {
                    "name": "rows",
                    "type": "integer",
                    "description": "Number of data rows to generate (1-100000; file transport is triggered above ~5000 rows)",
                    "required": False,
                    "default": 100,
                },
                {
                    "name": "columns",
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of column names to include; options: id / name / email / score / timestamp / description",
                    "required": False,
                },
            ],
        },
    ],
    "runtime": {
        "type": "uv",
        "min_version": "0.1.0",
    },
}


# ─── Tool Implementations ────────────────────────────────────────────


import hashlib
import string
import random as _random


def tool_word_count(text: str) -> dict:
    """Count the number of words, characters, and lines in text."""
    lines = text.split("\n")
    words = text.split()
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
        "characters_no_spaces": len(text.replace(" ", "")),
    }


def tool_text_transform(text: str, transform: str) -> dict:
    """Transform text format."""
    transforms = {
        "upper": str.upper,
        "lower": str.lower,
        "title": str.title,
        "reverse": lambda t: t[::-1],
    }
    fn = transforms.get(transform)
    if fn is None:
        return {
            "error": f"Unknown transform: {transform}. "
            f"Available: {', '.join(transforms.keys())}"
        }
    return {"original": text, "transformed": fn(text), "transform": transform}


def tool_text_repeat(text: str, count: int = 2, separator: str = " ") -> dict:
    """Repeat text a specified number of times."""
    count = max(1, min(100, count))  # clamp to 1..100
    result = separator.join([text] * count)
    return {"result": result, "count": count}


def tool_batch_word_count(texts: list) -> dict:
    """Batch word count for multiple texts (demonstrates array parameter usage)."""
    results = []
    for text in texts:
        words = text.split()
        results.append({"text_preview": text[:50], "words": len(words), "characters": len(text)})
    return {"count": len(results), "results": results}


def _make_fake_name(rng: _random.Random) -> str:
    """Generate a fake name."""
    first = rng.choice(["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
                        "Grace", "Hank", "Ivy", "Jack", "Karen", "Leo"])
    last = rng.choice(["Smith", "Johnson", "Williams", "Brown", "Jones",
                       "Garcia", "Miller", "Davis", "Wilson", "Taylor"])
    return f"{first} {last}"


def tool_generate_dataset(rows: int = 100, columns: list | None = None) -> dict:
    """Generate a mock dataset. Large row counts produce large JSON responses, triggering file transport."""
    rows = max(1, min(100000, rows))
    available_cols = ["id", "name", "email", "score", "timestamp", "description"]
    if not columns:
        columns = ["id", "name", "email", "score"]
    # Filter out invalid column names
    columns = [c for c in columns if c in available_cols] or ["id"]

    rng = _random.Random(42)  # Fixed seed for reproducibility
    dataset = []
    for i in range(rows):
        row = {}
        for col in columns:
            if col == "id":
                row["id"] = i + 1
            elif col == "name":
                row["name"] = _make_fake_name(rng)
            elif col == "email":
                name = _make_fake_name(rng).lower().replace(" ", ".")
                row["email"] = f"{name}@example.com"
            elif col == "score":
                row["score"] = round(rng.uniform(0, 100), 2)
            elif col == "timestamp":
                ts = 1700000000 + rng.randint(0, 10000000)
                row["timestamp"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            elif col == "description":
                words = rng.choices(string.ascii_lowercase.split() + [
                    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
                    "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
                    "incididunt", "ut", "labore", "et", "dolore", "magna",
                ], k=rng.randint(10, 30))
                row["description"] = " ".join(words)
        dataset.append(row)

    # Estimate response size
    sample_json = json.dumps(dataset[:1], ensure_ascii=False)
    estimated_bytes = len(sample_json.encode("utf-8")) * rows

    return {
        "rows": rows,
        "columns": columns,
        "estimated_bytes": estimated_bytes,
        "file_transport": estimated_bytes > MAX_STDIO_MESSAGE_BYTES,
        "dataset": dataset,
    }


# ─── Tool Dispatch Table ─────────────────────────────────────────────

TOOL_DISPATCH = {
    "word_count": tool_word_count,
    "text_transform": tool_text_transform,
    "text_repeat": tool_text_repeat,
    "batch_word_count": tool_batch_word_count,
    "generate_dataset": tool_generate_dataset,
}


# ─── JSON-RPC Handling ───────────────────────────────────────────────


def make_response(id, result=None, error=None):
    """Build a JSON-RPC 2.0 response."""
    resp = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def handle_describe(request_id):
    """Handle a describe request — return the tool self-description manifest."""
    return make_response(request_id, result=MANIFEST)


def handle_invoke(request_id, params):
    """Handle an invoke request — execute a tool call."""
    tool_name = params.get("tool")
    arguments = params.get("arguments", {})

    if not tool_name:
        return make_response(
            request_id,
            error={"code": -32602, "message": "Missing 'tool' in params"},
        )

    fn = TOOL_DISPATCH.get(tool_name)
    if fn is None:
        return make_response(
            request_id,
            error={
                "code": -32601,
                "message": f"Unknown tool: {tool_name}",
                "data": {"available_tools": list(TOOL_DISPATCH.keys())},
            },
        )

    try:
        result = fn(**arguments)
        return make_response(
            request_id,
            result={"success": True, "data": result, "tool": tool_name},
        )
    except TypeError as e:
        return make_response(
            request_id,
            error={"code": -32602, "message": f"Invalid parameters: {e}"},
        )
    except Exception as e:
        return make_response(
            request_id,
            error={"code": -32603, "message": f"Tool execution failed: {e}"},
        )


def handle_health(request_id):
    """Handle a health request — health check."""
    return make_response(
        request_id,
        result={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": MANIFEST["version"],
            "tools_count": len(MANIFEST["tools"]),
        },
    )


def handle_request(line: str) -> str:
    """Parse and handle a single JSON-RPC request."""
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
        response = handle_describe(request_id)
    elif method == "invoke":
        response = handle_invoke(request_id, params)
    elif method == "health":
        response = handle_health(request_id)
    else:
        response = make_response(
            request_id,
            error={"code": -32601, "message": f"Method not found: {method}"},
        )

    return json.dumps(response)


# ─── Response Sending (with File Transport Support) ──────────────────


def send_response(response_dict: dict) -> None:
    """Send a JSON-RPC response, using file transport automatically for large results.

    When the serialized JSON exceeds MAX_STDIO_MESSAGE_BYTES, the full response
    is written to a temporary file and only a lightweight pointer containing the
    file path is sent via stdout. The Agent will automatically delete the temp file
    after reading it.
    """
    payload = json.dumps(response_dict, ensure_ascii=False)
    payload_bytes = payload.encode("utf-8")

    if len(payload_bytes) > MAX_STDIO_MESSAGE_BYTES:
        # Write to a temporary file (Agent deletes it after reading)
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="executa-resp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            os.close(fd)
            raise

        # Send the file pointer via stdout
        pointer = json.dumps({
            "jsonrpc": "2.0",
            "id": response_dict.get("id"),
            "__file_transport": tmp_path,
        })
        print(
            f"📦 Response too large ({len(payload_bytes)} bytes), "
            f"using file transport: {tmp_path}",
            file=sys.stderr,
        )
        sys.stdout.write(pointer + "\n")
    else:
        sys.stdout.write(payload + "\n")

    sys.stdout.flush()


# ─── Main Loop (stdio JSON-RPC Service) ──────────────────────────────


def main():
    """Main entry point: reads JSON-RPC requests line by line from stdin and returns responses via stdout.

    Important: All log output goes to stderr to avoid interfering with protocol communication.
    """
    print("🔌 Example Executa plugin started", file=sys.stderr)
    print(f"   Tools: {list(TOOL_DISPATCH.keys())}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        print(f"← {line}", file=sys.stderr)

        response_str = handle_request(line)
        response_dict = json.loads(response_str)

        send_response(response_dict)

        print(f"→ (sent, {len(response_str)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
