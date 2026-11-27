"""Backend-owned task test for the background-check Executa.

The test plays the host on the plugin's stdio: it sends `invoke`, keeps an
in-memory KV for `storage/*`, and answers the other reverse RPCs. What it
pins down is which reverse RPCs belong to an invoke and which do not:

* issued while answering an invoke → carry that `context.invoke_id`;
* issued by the heartbeat or by a task after its invoke returned → carry no
  `context` at all, which is what makes the host treat them as background.

Run from executas/background-check-python:

    uv run --with pytest pytest ../../tests/plugin -v
"""

import copy
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN = (
    Path(__file__).resolve().parents[2]
    / "executas"
    / "background-check-python"
    / "background_check_plugin.py"
)

ANSWERS = {
    "web/search": {"results": [{"title": "t", "url": "https://example.com", "snippet": "s"}]},
    "web/image_search": {
        "results": [{"image_url": f"https://img.example/{n}.png"} for n in (1, 2, 3)]
    },
    "web/image_fetch": {"bytes_size": 43, "mime_type": "image/png"},
    "sampling/createMessage": {"role": "assistant", "content": {"type": "text", "text": "pong"}},
}


class Host:
    def __init__(self, kv=None, errors=None):
        self.kv = dict(kv or {})
        # reverse-RPC method → JSON-RPC error for every call, or a list of
        # errors for the first calls (later calls succeed).
        self.errors = dict(errors or {})
        self.calls = []  # every reverse RPC the plugin issued, in order
        self._responses = {}
        self._next_id = 0
        self._proc = subprocess.Popen(
            [sys.executable, str(PLUGIN)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        assert self.request("initialize", {"protocolVersion": "2.0"})["result"]["protocolVersion"] == "2.0"

    def _pump(self):
        for line in self._proc.stdout:
            self._lines.put(line)

    def close(self):
        self._proc.stdin.close()
        self._proc.wait(timeout=10)

    def _send(self, msg):
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _answer(self, call):
        self.calls.append(copy.deepcopy(call))
        method, params = call["method"], call["params"]
        error = self.errors.get(method)
        if isinstance(error, list):
            error = error.pop(0) if error else None
        if error:
            self._send({"jsonrpc": "2.0", "id": call["id"], "error": error})
            return
        if method == "storage/set":
            self.kv[params["key"]] = copy.deepcopy(params["value"])
            result = {"etag": "e1"}
        elif method == "storage/get":
            result = {"value": self.kv.get(params["key"]), "exists": params["key"] in self.kv}
        else:
            result = ANSWERS[method]
        self._send({"jsonrpc": "2.0", "id": call["id"], "result": result})

    def serve_until(self, done, timeout=15):
        """Answer reverse RPCs until `done()` is true."""
        deadline = time.monotonic() + timeout
        while not done():
            try:
                msg = json.loads(self._lines.get(timeout=max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                raise AssertionError(f"timed out; reverse RPCs so far: {[c['method'] for c in self.calls]}")
            if "method" in msg:
                self._answer(msg)
            else:
                self._responses[msg["id"]] = msg

    def request(self, method, params=None):
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        self.serve_until(lambda: req_id in self._responses)
        return self._responses.pop(req_id)

    def invoke(self, tool, arguments=None):
        """One invoke. Returns (tool data, invoke_id)."""
        invoke_id = f"inv-{self._next_id + 1}"
        response = self.request(
            "invoke", {"tool": tool, "arguments": arguments or {}, "context": {"invoke_id": invoke_id}}
        )
        assert "error" not in response, response
        return response["result"]["data"], invoke_id

    def wait_for_task(self, task_id, status):
        key = f"bg-check/{task_id}"
        self.serve_until(lambda: (self.kv.get(key) or {}).get("status") == status)
        return self.kv[key]


@pytest.fixture
def host():
    h = Host()
    yield h
    h.close()


def invoke_id_of(call):
    return (call["params"].get("context") or {}).get("invoke_id")


def test_heartbeat_starts_with_the_process_outside_any_invoke(host):
    host.serve_until(lambda: "bg-check/heartbeat" in host.kv)

    heartbeat = next(c for c in host.calls if c["params"].get("key") == "bg-check/heartbeat")
    assert "context" not in heartbeat["params"]
    assert heartbeat["params"]["scope"] == "app"

    data, _ = host.invoke("check_list")
    assert data["heartbeat"]["last_ok_at"] and data["heartbeat"]["last_error"] is None


def test_task_outlives_its_invoke_and_runs_in_the_background(host):
    host.serve_until(lambda: "bg-check/heartbeat" in host.kv)
    host.calls.clear()

    data, invoke_id = host.invoke("check_start", {"items": ["storage_kv", "web_search", "sampling"], "delay_s": 0.3})

    # The invoke answered before any check ran; what it wrote belongs to it.
    task_id = data["task_id"]
    assert data["task"]["status"] == "pending"
    assert host.calls and all(invoke_id_of(c) == invoke_id for c in host.calls)
    assert host.kv["bg-check/index"] == {"task_ids": [task_id]}

    in_invoke = len(host.calls)
    stored = host.wait_for_task(task_id, "done")

    # Everything after the invoke returned is background: no invoke_id.
    background = host.calls[in_invoke:]
    assert {c["method"] for c in background} >= {"web/search", "sampling/createMessage", "storage/set"}
    assert all("context" not in c["params"] for c in background)

    # The page gets the complete state back with one more invoke.
    status, _ = host.invoke("check_status", {"task_id": task_id})
    assert status["found"] and status["task"] == stored
    assert [(i["id"], i["status"]) for i in status["task"]["items"]] == [
        ("storage_kv", "ok"),
        ("web_search", "ok"),
        ("sampling", "ok"),
    ]
    assert status["task"]["items"][1]["detail"] == "1 results"

    listed, _ = host.invoke("check_list")
    assert [t["task_id"] for t in listed["tasks"]] == [task_id]


def test_failed_check_is_a_row_not_the_end_of_the_task():
    denied = {"code": -32021, "message": "storage_token missing (outside invoke context?)"}
    host = Host(errors={"web/search": denied})
    try:
        data, _ = host.invoke("check_start", {"items": ["web_search", "sampling"], "delay_s": 0})
        task = host.wait_for_task(data["task_id"], "done")
    finally:
        host.close()

    failed, ok = task["items"]
    assert failed["status"] == "failed" and failed["error"] == denied
    assert ok["status"] == "ok"


def _run_image_fetch(errors):
    host = Host(errors={"web/image_fetch": errors})
    try:
        data, _ = host.invoke("check_start", {"items": ["web_image_fetch"], "delay_s": 0})
        (item,) = host.wait_for_task(data["task_id"], "done")["items"]
    finally:
        host.close()
    fetched = [c["params"]["url"] for c in host.calls if c["method"] == "web/image_fetch"]
    return item, fetched


def test_image_fetch_moves_past_a_source_that_cannot_be_downloaded():
    unreachable = {"code": -32523, "message": "image fetch TIMEOUT"}

    item, fetched = _run_image_fetch([unreachable])

    assert fetched == ["https://img.example/1.png", "https://img.example/2.png"]
    assert item["status"] == "ok"
    assert item["detail"] == "stored 43 bytes (image/png); skipped 1 unreachable source(s)"


def test_image_fetch_fails_when_no_source_can_be_downloaded():
    unreachable = {"code": -32523, "message": "HTTP_403"}

    item, fetched = _run_image_fetch(unreachable)

    assert len(fetched) == 3
    assert item["status"] == "failed" and item["error"] == unreachable


def test_image_fetch_reports_a_caller_side_error_without_trying_other_sources():
    denied = {"code": -32521, "message": "executa 'x' is disabled by user"}

    item, fetched = _run_image_fetch(denied)

    assert fetched == ["https://img.example/1.png"]
    assert item["status"] == "failed" and item["error"] == denied


def test_cancel_stops_a_waiting_task(host):
    data, _ = host.invoke("check_start", {"items": ["web_search"], "delay_s": 60})

    cancelled, _ = host.invoke("check_cancel", {"task_id": data["task_id"]})

    assert cancelled["cancelled"] is True
    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["task"]["items"][0]["status"] == "cancelled"
    assert not any(c["method"] == "web/search" for c in host.calls)


def test_restart_reports_unfinished_work_as_interrupted():
    """A new process finds a task the previous one was still running."""
    task = {
        "task_id": "1700000000-abc123",
        "status": "running",
        "items": [
            {"id": "storage_kv", "status": "ok"},
            {"id": "web_search", "status": "pending"},
        ],
    }
    host = Host(kv={"bg-check/index": {"task_ids": [task["task_id"]]}, f"bg-check/{task['task_id']}": task})
    try:
        listed, _ = host.invoke("check_list")
    finally:
        host.close()

    (restored,) = listed["tasks"]
    assert restored["status"] == "interrupted"
    assert [i["status"] for i in restored["items"]] == ["ok", "interrupted"]
    assert host.kv[f"bg-check/{task['task_id']}"]["status"] == "interrupted"
    assert not any(c["method"] == "web/search" for c in host.calls)


def test_unknown_check_is_rejected(host):
    response = host.request(
        "invoke", {"tool": "check_start", "arguments": {"items": ["nope"]}, "context": {"invoke_id": "inv-x"}}
    )
    assert response["error"]["code"] == -32602
