"""Tests for executa_sdk.progress.emit_progress (Phase E)."""

from __future__ import annotations

import io
import json

from executa_sdk import bind_invoke, emit_progress
from executa_sdk.progress import METHOD_EXECUTA_PROGRESS


def _emit_to_buffer(**kwargs):
    buf = io.StringIO()
    ok = emit_progress(stdout=buf, **kwargs)
    return ok, buf.getvalue()


def test_emit_with_explicit_invoke_id():
    ok, raw = _emit_to_buffer(
        type_="tool_update",
        data={"step": 1, "total": 3},
        invoke_id="tjob_" + "a" * 32,
    )
    assert ok is True
    frame = json.loads(raw)
    assert frame["method"] == METHOD_EXECUTA_PROGRESS
    assert "id" not in frame  # notification — the host must not respond
    assert frame["params"]["type"] == "tool_update"
    assert frame["params"]["data"] == {"step": 1, "total": 3}
    assert frame["params"]["context"]["invoke_id"] == "tjob_" + "a" * 32


def test_emit_uses_bound_invoke_id():
    params = {"context": {"invoke_id": "tjob_" + "b" * 32}}
    with bind_invoke(params):
        ok, raw = _emit_to_buffer(type_="progress", data={"note": "hi"})
    assert ok is True
    frame = json.loads(raw)
    assert frame["params"]["context"]["invoke_id"] == "tjob_" + "b" * 32


def test_emit_without_invoke_id_is_skipped():
    ok, raw = _emit_to_buffer(type_="progress", data={})
    assert ok is False
    assert raw == ""


def test_unknown_type_coerced_to_progress():
    ok, raw = _emit_to_buffer(type_="completed", data={}, invoke_id="x")
    assert ok is True
    assert json.loads(raw)["params"]["type"] == "progress"


def test_write_failure_returns_false():
    class Boom:
        def write(self, _):
            raise OSError("pipe closed")

        def flush(self):
            pass

    assert emit_progress("progress", {}, invoke_id="x", stdout=Boom()) is False
