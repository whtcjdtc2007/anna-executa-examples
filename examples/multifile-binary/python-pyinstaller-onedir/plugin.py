#!/usr/bin/env python3
"""Multi-file binary plugin example — Python + PyInstaller --onedir.

Demonstrates a binary plugin that ships **with** its own bundled
sibling files (shared libraries, data, sub-tools). When the plugin
is installed by Anna Agent the layout becomes::

    ~/.anna/executa/tools/{tool_id}/current/
      bin/example-multifile-tool        ← entrypoint
      lib/                              ← .so / .dylib / .pyd live here
      data/greeting.txt                 ← sample bundled data
      manifest.json

The Agent injects ``EXECUTA_HOME``, ``EXECUTA_DATA``, and the
platform-appropriate ``LD_LIBRARY_PATH`` / ``DYLD_LIBRARY_PATH``
into the child process. This script demonstrates discovering its
own siblings using those variables.

Run from source::

    python plugin.py

Build a multi-file binary::

    ./build.sh

The resulting ``dist/example-multifile-tool/`` (PyInstaller --onedir)
should be packed into ``example-multifile-tool-{platform}.tar.gz``
and uploaded to the Nexus ``binary_urls`` field, e.g.::

    {
      "darwin-arm64": {
        "url": "https://your-cdn.example.com/example-multifile-tool-darwin-arm64.tar.gz",
        "sha256": "...",
        "entrypoint": "bin/example-multifile-tool"
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = {
    "name": "example-multifile-tool",
    "display_name": "Multi-file Binary Example",
    "version": "1.0.0",
    "description": "Example plugin shipping with sibling bundled resources (lib/ + data/)",
    "author": "Anna Developer",
    "tools": [
        {
            "name": "describe_layout",
            "description": "Inspect the on-disk install layout this plugin sees at runtime",
            "parameters": [],
        },
        {
            "name": "read_bundled_greeting",
            "description": "Read the bundled data/greeting.txt that ships next to the binary",
            "parameters": [],
        },
    ],
    # Multi-file binary contract: declares which file inside the archive
    # is the entrypoint. Agent v2 reads this when extracting; without it
    # the Agent would warn and pick the first executable it finds.
    "runtime": {
        "binary": {
            "entrypoint": "bin/example-multifile-tool",
            "lib_dirs": ["lib"],
            "data_dirs": ["data"],
        }
    },
}


def _tool_home() -> Path:
    """Resolve the install root the Agent injected via env, with fallback."""
    explicit = os.environ.get("EXECUTA_HOME")
    if explicit:
        return Path(explicit)
    # Fallback for ad-hoc dev runs: walk up from the executable.
    return Path(sys.argv[0]).resolve().parent.parent


def _data_dir() -> Path:
    explicit = os.environ.get("EXECUTA_DATA")
    if explicit:
        return Path(explicit)
    return _tool_home() / "data"


def handle_describe() -> dict:
    return MANIFEST


def handle_invoke(params: dict) -> dict:
    name = params.get("name", "")
    if name == "describe_layout":
        home = _tool_home()
        return {
            "executa_home": str(home),
            "executa_data": str(_data_dir()),
            "lib_dir_exists": (home / "lib").is_dir(),
            "data_dir_exists": (home / "data").is_dir(),
            "platform_key": f"{sys.platform}-{os.uname().machine}" if hasattr(os, "uname") else sys.platform,
            "argv0": sys.argv[0],
            "ld_library_path": os.environ.get("LD_LIBRARY_PATH", ""),
            "dyld_library_path": os.environ.get("DYLD_LIBRARY_PATH", ""),
        }
    if name == "read_bundled_greeting":
        path = _data_dir() / "greeting.txt"
        if not path.is_file():
            raise FileNotFoundError(f"greeting.txt missing at {path}")
        return {"path": str(path), "content": path.read_text(encoding="utf-8").strip()}
    raise ValueError(f"unknown tool: {name}")


def handle_health() -> dict:
    return {"status": "ready", "message": "OK", "details": {"started_at": datetime.now(timezone.utc).isoformat()}}


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"invalid JSON: {exc}\n")
            continue
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        try:
            if method == "describe":
                result = handle_describe()
            elif method == "invoke":
                result = handle_invoke(params)
            elif method == "health":
                result = handle_health()
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as exc:  # pragma: no cover - error surface
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32603, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
