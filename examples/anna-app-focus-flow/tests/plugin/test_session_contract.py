"""Plugin contract test for the focus-session Executa.

Run with: pytest tests/plugin
Requires: anna-executa-test (installed in the focus-session venv).
"""
from pathlib import Path

import pytest
from anna_executa_test import assert_jsonrpc_ok, executa

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "executas" / "focus-session"


@pytest.fixture(scope="module")
def plugin():
    with executa.spawn(PLUGIN_DIR) as p:
        yield p


def test_describe_advertises_session_tool(plugin):
    info = plugin.call("describe")
    assert info["name"].startswith("tool-")
    tool_names = {t["name"] for t in info["tools"]}
    assert "session" in tool_names


def test_get_state_returns_envelope(plugin):
    resp = plugin.call(
        "invoke",
        {"tool": "session", "arguments": {"action": "get_state"}},
    )
    assert_jsonrpc_ok(resp)
    # The plugin always returns an "active" key (None when idle).
    assert "active" in resp["data"]


def test_start_then_complete_round_trip(plugin):
    start = plugin.call(
        "invoke",
        {
            "tool": "session",
            "arguments": {
                "action": "start",
                "duration_minutes": 1,
                "topic": "contract test",
            },
        },
    )
    assert_jsonrpc_ok(start)
    assert start["data"]["active"] is not None

    complete = plugin.call(
        "invoke",
        {
            "tool": "session",
            "arguments": {"action": "complete", "notes": "done"},
        },
    )
    assert_jsonrpc_ok(complete)
    assert complete["data"]["active"] is None
