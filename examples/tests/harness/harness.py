"""Run a demo Executa on the real ``anna-app dev`` Python runtime.

The plugin is a real subprocess speaking stdio JSON-RPC to the runtime's
``ExecutaPool``; its reverse RPCs go through the runtime's own
``LocalServerRequestHandler``. Only the last hop is faked: instead of the
Node bridge calling nexus, ``host_call`` records what the runtime asked for
and answers with a canned result. So a test fails when the runtime does not
route a method the plugin uses — the forum #598 class of bug — without any
network access.

This is the ONLY file that imports ``anna_app_runtime_local`` internals; the
package is still alpha, so keep the coupling here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from anna_app_runtime_local import LocalDispatcherSession
from anna_app_runtime_local.executa import ExecutaPool, ExecutaSpec
from anna_app_runtime_local.session import LocalServerRequestHandler
from anna_app_runtime_local.store import InMemoryWindowStore

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
TOOL_ID = "tool-under-test"

_MANIFEST = {
    "schema": 2,
    "permissions": [],
    "required_executas": [],
    "optional_executas": [],
    "ui": {
        "bundle": {"entry": "index.html"},
        "views": [{"name": "main", "title": "Main", "default": True}],
        "host_api": {},
    },
}

HostResponse = dict | Callable[[dict], dict]


class Harness:
    """One plugin process + one runtime session.

    ``responses`` maps a ``host.<ns>.<method>`` to the result the fake host
    returns (a dict, or a callable taking the forwarded args); unlisted
    methods answer ``{}``. ``host_calls`` is everything the runtime forwarded.
    """

    def __init__(self, plugin: str, responses: dict[str, HostResponse] | None = None):
        script = EXAMPLES_DIR / plugin
        assert script.is_file(), script
        self.responses = responses or {}
        self.host_calls: list[tuple[str, dict]] = []
        # Same switches as `anna-app dev --storage aps --web real`: every
        # reverse RPC that can leave the runtime does, and lands in host_call.
        store = InMemoryWindowStore(
            host_call=self._host_call,
            aps_forward_storage=True,
            web_forward_real=True,
        )
        self._session = LocalDispatcherSession.create(
            user_id=1, manifest_dict=_MANIFEST, store=store
        )
        self._pool = ExecutaPool(
            {
                TOOL_ID: ExecutaSpec(
                    tool_id=TOOL_ID,
                    project_dir=str(script.parent),
                    command=[sys.executable, str(script)],
                )
            }
        )
        self._pool.set_server_request_handler(LocalServerRequestHandler())

    async def _host_call(self, method: str, args: dict) -> dict:
        self.host_calls.append((method, dict(args)))
        response = self.responses.get(method, {})
        return response(args) if callable(response) else response

    @property
    def host_methods(self) -> list[str]:
        return [method for method, _ in self.host_calls]

    async def invoke(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """``tools.invoke`` — returns the tool's data, or fails the test with
        the plugin's error."""
        outer = await self._pool.invoke(
            tool_id=TOOL_ID,
            tool_name=tool,
            tool_args=args or {},
            timeout_ms=30_000,
            invoke_context={"session": self._session, "user_id": 1},
        )
        result = outer.get("data") or {}
        assert outer.get("success") and result.get("success"), outer
        return result.get("data")

    async def close(self) -> None:
        await self._pool.shutdown()
