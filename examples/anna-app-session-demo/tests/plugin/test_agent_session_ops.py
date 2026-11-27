"""Op-dispatch test for the session-via-executa Executa.

The test plays the host on the plugin's stdio: it sends `invoke`, answers
the `agent/session.*` reverse RPCs the plugin issues, and pushes live
`agent/session.frame` notifications.

Run from executas/session-via-executa-python:

    uv run --with pytest --with anna-executa-test pytest ../../tests/plugin -v
"""
import json
import queue
import threading
import time
from pathlib import Path

import pytest
from anna_executa_test import executa

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "executas" / "session-via-executa-python"
UUID = "aps_test_0001"
REFRESHED = {"app_session_uuid": UUID, "expires_in": 600, "kind": "agent", "agent_submode": "auto"}


class Host:
    def __init__(self, client):
        self._stdin = client.proc.stdin
        self._lines = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._pump, args=(client.proc.stdout,), daemon=True).start()

    def _pump(self, stdout):
        for line in stdout:
            self._lines.put(line)

    def send(self, msg):
        self._stdin.write(json.dumps(msg) + "\n")
        self._stdin.flush()

    def read(self):
        return json.loads(self._lines.get(timeout=15))

    def request(self, method, params=None):
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        return self._next_id

    def start_invoke(self, arguments):
        invoke_id = f"inv-{self._next_id + 1}"
        req_id = self.request(
            "invoke",
            {"tool": "agent_session", "arguments": arguments, "context": {"invoke_id": invoke_id}},
        )
        return req_id, invoke_id

    def reply(self, call, result=None, error=None):
        body = {"error": error} if error else {"result": result}
        self.send({"jsonrpc": "2.0", "id": call["id"], **body})

    def invoke(self, arguments, answers=None):
        """One invoke, answered from `answers` (reverse-RPC method → result).

        Returns the invoke response and the reverse RPCs the plugin issued.
        """
        req_id, invoke_id = self.start_invoke(arguments)
        calls = []
        while True:
            msg = self.read()
            if msg.get("id") == req_id and "method" not in msg:
                return msg, calls
            assert msg["params"]["context"]["invoke_id"] == invoke_id
            calls.append(msg)
            self.reply(msg, result=(answers or {})[msg["method"]])


@pytest.fixture(scope="module")
def host():
    with executa.spawn(PLUGIN_DIR) as client:
        h = Host(client)
        h.request("initialize", {"protocolVersion": "2.0"})
        assert h.read()["result"]["protocolVersion"] == "2.0"
        yield h


def data_of(response):
    return response["result"]["data"]


def test_describe_exposes_one_tool_with_eleven_ops(host):
    host.request("describe")
    tools = host.read()["result"]["tools"]
    assert [t["name"] for t in tools] == ["agent_session"]
    op = next(p for p in tools[0]["parameters"] if p["name"] == "op")
    assert op["enum"] == [
        "create", "attach", "list", "refresh", "run", "cancel",
        "history", "delete", "run_status", "frames", "catalog",
    ]


OP_CASES = [
    (
        {"op": "create", "submode": "fixed", "fixed_client_id": "c1", "system_prompt": "be terse"},
        [("agent/session.create", {"agent_submode": "fixed", "fixed_client_id": "c1", "system_prompt": "be terse"})],
        {"agent/session.create": {"app_session_uuid": UUID, "agent_submode": "fixed", "fixed_client_id": "c1", "granted_tools": ["fs_read_file"]}},
        {"app_session_uuid": UUID, "submode": "fixed", "granted_tools": ["fs_read_file"], "inherit_host_tools": False},
    ),
    (
        {"op": "attach", "app_session_uuid": UUID},
        [("agent/session.refresh", {"app_session_uuid": UUID})],
        {"agent/session.refresh": REFRESHED},
        {"app_session_uuid": UUID, "submode": "auto"},
    ),
    (
        {"op": "list", "limit": 5},
        [("agent/session.list", {"limit": 5, "include_expired": False})],
        {"agent/session.list": {"sessions": [{"app_session_uuid": UUID}]}},
        {"sessions": [{"app_session_uuid": UUID}]},
    ),
    (
        {"op": "refresh", "app_session_uuid": UUID},
        [("agent/session.refresh", {"app_session_uuid": UUID})],
        {"agent/session.refresh": REFRESHED},
        REFRESHED,
    ),
    (
        {"op": "cancel", "app_session_uuid": UUID, "run_id": "run_1"},
        [("agent/session.refresh", {}), ("agent/session.cancel", {"app_session_uuid": UUID, "run_id": "run_1"})],
        {"agent/session.refresh": REFRESHED, "agent/session.cancel": {"cancelled": True}},
        {"cancelled": True},
    ),
    (
        {"op": "history", "app_session_uuid": UUID},
        [("agent/session.refresh", {}), ("agent/session.history", {"app_session_uuid": UUID})],
        {"agent/session.refresh": REFRESHED, "agent/session.history": {"messages": []}},
        {"messages": []},
    ),
    (
        {"op": "delete", "app_session_uuid": UUID},
        [("agent/session.refresh", {}), ("agent/session.delete", {"app_session_uuid": UUID})],
        {"agent/session.refresh": REFRESHED, "agent/session.delete": {"deleted": True}},
        {"deleted": True},
    ),
    (
        {"op": "run_status", "app_session_uuid": UUID, "run_id": "run_1"},
        [("agent/session.run.status", {"app_session_uuid": UUID, "run_id": "run_1"})],
        {"agent/session.run.status": {"status": "completed", "stream_id": "s1"}},
        {"status": "completed", "stream_id": "s1"},
    ),
    (
        {"op": "frames", "stream_id": "s1", "after_seq": 3},
        [("agent/session.frames", {"stream_id": "s1", "after_seq": 3, "limit": 500})],
        {"agent/session.frames": {"frames": [], "complete": True}},
        {"frames": [], "complete": True},
    ),
    (
        {"op": "catalog"},
        [("agent/session.catalog", {})],
        {"agent/session.catalog": {"platform_tools": [], "manifest_declared": []}},
        {"platform_tools": [], "manifest_declared": []},
    ),
]


@pytest.mark.parametrize(
    ("arguments", "expected_calls", "answers", "expected_result"),
    OP_CASES,
    ids=[case[0]["op"] for case in OP_CASES],
)
def test_op_issues_its_reverse_rpc(host, arguments, expected_calls, answers, expected_result):
    response, calls = host.invoke(arguments, answers)

    assert [c["method"] for c in calls] == [method for method, _ in expected_calls]
    for call, (_, params) in zip(calls, expected_calls):
        assert params.items() <= call["params"].items()
    data = data_of(response)
    assert (data["op"], data["ok"]) == (arguments["op"], True)
    assert expected_result.items() <= data["result"].items()


def test_run_yields_live_frames_then_the_rest_of_the_response(host):
    req_id, _ = host.start_invoke({"op": "run", "app_session_uuid": UUID, "content": "hi", "run_id": "run_live"})
    host.reply(host.read(), result=REFRESHED)
    run = host.read()
    assert run["method"] == "agent/session.run"
    assert {"app_session_uuid": UUID, "content": "hi", "run_id": "run_live", "stream": True}.items() <= run["params"].items()

    frames = [{"event": "delta", "text": t, "seq": i} for i, t in enumerate(["a", "b", "c"])]
    for frame in frames[:2]:
        host.send({"jsonrpc": "2.0", "method": "agent/session.frame", "params": {"run_id": "run_live", "seq": frame["seq"], "frame": frame}})
    time.sleep(0.3)  # the run is still in flight; only the live frames are out
    host.reply(run, result={"run_id": "run_live", "stream_id": "strm_x", "frames": frames})

    response = host.read()
    assert response["id"] == req_id
    result = data_of(response)["result"]
    assert result["run_id"] == "run_live"
    assert result["frames"] == frames
    assert result["elapsed_ms"] - result["first_frame_ms"] >= 200


def test_cancel_is_answered_while_its_run_is_in_flight(host):
    run_req, _ = host.start_invoke({"op": "run", "app_session_uuid": UUID, "content": "long task", "run_id": "run_slow"})
    host.reply(host.read(), result=REFRESHED)
    pending_run = host.read()
    assert pending_run["method"] == "agent/session.run"

    cancel_response, calls = host.invoke(
        {"op": "cancel", "app_session_uuid": UUID, "run_id": "run_slow"},
        {"agent/session.refresh": REFRESHED, "agent/session.cancel": {"cancelled": True, "run_id": "run_slow"}},
    )
    assert data_of(cancel_response)["result"] == {"cancelled": True, "run_id": "run_slow"}
    assert calls[-1]["params"]["run_id"] == "run_slow"

    host.reply(pending_run, result={"run_id": "run_slow", "stream_id": "strm_y", "frames": []})
    assert host.read()["id"] == run_req


def test_session_not_found_is_an_in_band_answer(host):
    req_id, _ = host.start_invoke({"op": "attach", "app_session_uuid": "aps_gone"})
    host.reply(
        host.read(),
        error={"code": -32042, "message": "unknown app_session_uuid=aps_gone", "data": {"errorCode": "SESSION_NOT_FOUND"}},
    )
    response = host.read()
    assert response["id"] == req_id
    assert data_of(response) == {
        "op": "attach",
        "ok": False,
        "error": {"code": -32042, "name": "SESSION_NOT_FOUND", "message": "unknown app_session_uuid=aps_gone"},
    }


def test_missing_session_uuid_is_invalid_params(host):
    response, calls = host.invoke({"op": "history"})
    assert calls == []
    assert response["error"]["code"] == -32602
