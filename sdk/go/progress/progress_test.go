package progress

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestEmitWritesNotification(t *testing.T) {
	var buf bytes.Buffer
	ok := Emit(&buf, "tjob_abc", "tool_update", map[string]any{"step": 1})
	if !ok {
		t.Fatal("Emit returned false")
	}
	var frame map[string]any
	if err := json.Unmarshal(buf.Bytes(), &frame); err != nil {
		t.Fatalf("bad JSON: %v", err)
	}
	if frame["method"] != MethodExecutaProgress {
		t.Fatalf("method = %v", frame["method"])
	}
	if _, hasID := frame["id"]; hasID {
		t.Fatal("notification must not carry an id")
	}
	params := frame["params"].(map[string]any)
	if params["type"] != "tool_update" {
		t.Fatalf("type = %v", params["type"])
	}
	ctx := params["context"].(map[string]any)
	if ctx["invoke_id"] != "tjob_abc" {
		t.Fatalf("invoke_id = %v", ctx["invoke_id"])
	}
}

func TestEmitCoercesUnknownType(t *testing.T) {
	var buf bytes.Buffer
	if !Emit(&buf, "x", "completed", nil) {
		t.Fatal("Emit returned false")
	}
	var frame map[string]any
	_ = json.Unmarshal(buf.Bytes(), &frame)
	if frame["params"].(map[string]any)["type"] != "progress" {
		t.Fatal("unknown type not coerced to progress")
	}
}

func TestEmitRequiresInvokeID(t *testing.T) {
	var buf bytes.Buffer
	if Emit(&buf, "", "progress", nil) {
		t.Fatal("Emit should return false without invoke id")
	}
	if buf.Len() != 0 {
		t.Fatal("nothing should be written")
	}
}
