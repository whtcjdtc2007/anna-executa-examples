// Package progress emits plugin → host progress notifications for
// long-running tool jobs (the `anna.tools.invokeAsync` channel).
//
// Design: matrix-nexus docs/design/anna-app-tools-invoke-async-jobs.md
// §4.2 / Phase E. Mirrors executa_sdk.progress (Python) and progress.js
// (Node) — keep the three in sync.
//
// Wire shape — a JSON-RPC NOTIFICATION (no `id`; the host never responds):
//
//	{"jsonrpc":"2.0","method":"executa/progress",
//	 "params":{"type":"tool_update","data":{...},
//	           "context":{"invoke_id":"<parent invoke>"}}}
//
// Host-side semantics (silent drop by design):
//   - Type must be "progress" or "tool_update" — anything else is coerced
//     to "progress" (terminal states cannot be faked).
//   - The parent invoke id must reference an ACTIVE invoke; unknown /
//     finished ids are dropped.
//   - Rate limit: 50 events/second per invoke — excess is dropped.
//   - Only ASYNC job invokes (tools.invokeAsync) have a progress channel;
//     during a plain synchronous tools.invoke the events are dropped.
//   - Keep data small (host stores ≤8KB per event).
//
// Example:
//
//	ctx := invokectx.FromParams(req.Params)
//	for i := 0; i < total; i++ {
//	    doStep(i)
//	    progress.Emit(os.Stdout, ctx.InvokeID, "tool_update",
//	        map[string]any{"step": i + 1, "total": total})
//	}
package progress

import (
	"encoding/json"
	"io"
)

// MethodExecutaProgress is the JSON-RPC notification method name.
const MethodExecutaProgress = "executa/progress"

// Emit writes one progress notification to w (normally os.Stdout, the
// plugin's stdio channel to the host). Best-effort: returns false when
// invokeID is empty or the write fails; never panics. eventType is
// coerced to "progress" unless it is "tool_update".
func Emit(w io.Writer, invokeID, eventType string, data map[string]any) bool {
	if invokeID == "" || w == nil {
		return false
	}
	if eventType != "progress" && eventType != "tool_update" {
		eventType = "progress"
	}
	if data == nil {
		data = map[string]any{}
	}
	frame := map[string]any{
		"jsonrpc": "2.0",
		"method":  MethodExecutaProgress,
		"params": map[string]any{
			"type":    eventType,
			"data":    data,
			"context": map[string]any{"invoke_id": invokeID},
		},
	}
	buf, err := json.Marshal(frame)
	if err != nil {
		return false
	}
	buf = append(buf, '\n')
	if _, err := w.Write(buf); err != nil {
		return false
	}
	return true
}
