package sampling

import (
	"encoding/json"
	"testing"
	"time"
)

type fakeHost struct {
	frames []map[string]any
}

func (h *fakeHost) write(msg map[string]any) error {
	h.frames = append(h.frames, msg)
	return nil
}

func (h *fakeHost) waitFrames(n int) bool {
	for i := 0; i < 500; i++ {
		if len(h.frames) >= n {
			return true
		}
		time.Sleep(time.Millisecond)
	}
	return false
}

var testMessages = []Message{{Role: "user", Content: MessageContent{Type: "text", Text: "hi"}}}

// roundtrip issues CreateMessage, replies with a fixed result and returns the
// JSON-serialized wire params (so omitempty behaviour is exercised).
func roundtrip(t *testing.T, req CreateMessageRequest) map[string]any {
	t.Helper()
	h := &fakeHost{}
	c := New(h.write)
	done := make(chan struct{})
	var res *CreateMessageResult
	var err error
	go func() {
		res, err = c.CreateMessage(req, 0)
		close(done)
	}()
	if !h.waitFrames(1) {
		t.Fatal("no frame")
	}
	if h.frames[0]["method"] != MethodSamplingCreateMessage {
		t.Fatalf("method %v", h.frames[0]["method"])
	}
	raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": h.frames[0]["id"], "result": map[string]any{
		"role": "assistant", "content": map[string]any{"type": "text", "text": "ok"}, "model": "m", "stopReason": "endTurn",
		"usage": map[string]any{"inputTokens": 1, "outputTokens": 1, "totalTokens": 2, "reasoningTokens": 7},
	}})
	c.DispatchResponse(raw)
	<-done
	if err != nil {
		t.Fatal(err)
	}
	if res.Usage.ReasoningTokens != 7 {
		t.Fatalf("reasoningTokens %d", res.Usage.ReasoningTokens)
	}
	buf, _ := json.Marshal(h.frames[0]["params"])
	var params map[string]any
	_ = json.Unmarshal(buf, &params)
	return params
}

func TestThinkingOmittedWhenNil(t *testing.T) {
	params := roundtrip(t, CreateMessageRequest{Messages: testMessages, MaxTokens: 16})
	if _, ok := params["thinking"]; ok {
		t.Fatalf("thinking should be omitted: %v", params)
	}
	if _, ok := params["responseFormat"]; ok {
		t.Fatalf("responseFormat should be omitted: %v", params)
	}
}

func TestThinkingFalseForwarded(t *testing.T) {
	params := roundtrip(t, CreateMessageRequest{Messages: testMessages, MaxTokens: 16, Thinking: false})
	if v, ok := params["thinking"].(bool); !ok || v {
		t.Fatalf("thinking %v", params["thinking"])
	}
}

func TestThinkingObjectForwarded(t *testing.T) {
	enabled, include := true, true
	params := roundtrip(t, CreateMessageRequest{Messages: testMessages, MaxTokens: 16, Thinking: &Thinking{
		Enabled: &enabled, Effort: "low", BudgetTokens: 1024, IncludeReasoning: &include,
	}})
	got, _ := params["thinking"].(map[string]any)
	if got["enabled"] != true || got["effort"] != "low" || got["budgetTokens"] != float64(1024) || got["includeReasoning"] != true {
		t.Fatalf("thinking %v", params["thinking"])
	}
}
