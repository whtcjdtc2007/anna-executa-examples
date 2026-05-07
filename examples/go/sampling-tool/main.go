// sampling_tool — Executa plugin (Go) that uses host LLM sampling.
//
// Demonstrates:
//   - the v2 `initialize` handshake (advertises client_capabilities.sampling)
//   - issuing a reverse `sampling/createMessage` request to the host
//   - sharing one stdin reader between agent invokes and host responses
//
// To enable end-to-end:
//  1. Declare host_capabilities: ["llm.sample"] in the published manifest.
//  2. The user must toggle sampling_grant.enabled = true in Anna Admin.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	sdk "github.com/anna-executa/sdk/go/sampling"
)

// ─── Manifest ────────────────────────────────────────────────────────

var manifest = map[string]any{
	"name":         "sampling-summarizer-go",
	"display_name": "Sampling Summarizer (Go)",
	"version":      "0.1.0",
	"description":  "Summarizes text by asking the host to sample an LLM.",
	"author":       "Anna Developer",
	// NEW in v2 — declares the reverse capabilities this plugin will use.
	"host_capabilities": []string{"llm.sample"},
	"tools": []map[string]any{
		{
			"name":        "summarize",
			"description": "Summarize the supplied text into one short paragraph.",
			"parameters": []map[string]any{
				{"name": "text", "type": "string", "description": "Text to summarize", "required": true},
				{"name": "max_words", "type": "integer", "description": "Approx max words", "required": false, "default": 80},
			},
		},
	},
	"runtime": map[string]any{"type": "binary"},
}

// ─── Wiring ──────────────────────────────────────────────────────────

var (
	stdoutMu sync.Mutex
	sampling *sdk.Client
)

func writeFrame(msg map[string]any) error {
	buf, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	stdoutMu.Lock()
	defer stdoutMu.Unlock()
	_, err = os.Stdout.Write(append(buf, '\n'))
	return err
}

func makeResponse(id any, result any, errObj map[string]any) map[string]any {
	out := map[string]any{"jsonrpc": "2.0", "id": id}
	if errObj != nil {
		out["error"] = errObj
	} else {
		out["result"] = result
	}
	return out
}

// ─── Tool ────────────────────────────────────────────────────────────

func handleSummarize(args map[string]any, invokeID string) (map[string]any, error) {
	text, _ := args["text"].(string)
	if strings.TrimSpace(text) == "" {
		return map[string]any{"summary": "", "note": "empty input"}, nil
	}
	maxWords := 80
	switch v := args["max_words"].(type) {
	case float64:
		maxWords = int(v)
	case int:
		maxWords = v
	}
	if maxWords < 20 {
		maxWords = 20
	}
	if maxWords > 400 {
		maxWords = 400
	}
	maxTokens := maxWords * 5
	if maxTokens < 64 {
		maxTokens = 64
	}
	if maxTokens > 1024 {
		maxTokens = 1024
	}

	res, err := sampling.CreateMessage(sdk.CreateMessageRequest{
		Messages: []sdk.Message{
			{
				Role: "user",
				Content: sdk.MessageContent{
					Type: "text",
					Text: fmt.Sprintf(
						"Summarize the following text in at most %d words. Return only the summary, no preamble.\n\n---\n%s",
						maxWords, text,
					),
				},
			},
		},
		MaxTokens:    maxTokens,
		SystemPrompt: "You are a concise editorial assistant.",
		// No ModelPreferences → host falls back to user's preferred_model.
		Metadata: map[string]string{"executa_invoke_id": invokeID, "tool": "summarize"},
	}, 60*time.Second)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"summary":    res.Content.Text,
		"model":      res.Model,
		"usage":      res.Usage,
		"stopReason": res.StopReason,
	}, nil
}

// ─── Dispatch ────────────────────────────────────────────────────────

func handleInitialize(reqID any, params map[string]any) map[string]any {
	proto, _ := params["protocolVersion"].(string)
	if proto == "" {
		proto = "1.1"
	}
	if proto != sdk.ProtocolVersionV2 {
		sampling.Disable(fmt.Sprintf(
			"host did not negotiate v2 (offered protocolVersion=%q); sampling/createMessage requires Executa protocol 2.0",
			proto,
		))
	}
	clientCaps := map[string]any{}
	if proto == sdk.ProtocolVersionV2 {
		clientCaps["sampling"] = map[string]any{}
	}
	negotiated := proto
	if negotiated != sdk.ProtocolVersionV2 {
		negotiated = "1.1"
	}
	return makeResponse(reqID, map[string]any{
		"protocolVersion":     negotiated,
		"serverInfo":          map[string]any{"name": manifest["name"], "version": manifest["version"]},
		"client_capabilities": clientCaps,
		"capabilities":        map[string]any{},
	}, nil)
}

func handleInvoke(reqID any, params map[string]any) map[string]any {
	tool, _ := params["tool"].(string)
	args, _ := params["arguments"].(map[string]any)
	invokeID, _ := params["invoke_id"].(string)

	if tool != "summarize" {
		return makeResponse(reqID, nil, map[string]any{"code": -32601, "message": "Unknown tool: " + tool})
	}
	data, err := handleSummarize(args, invokeID)
	if err != nil {
		var se *sdk.SamplingError
		if errors.As(err, &se) {
			return makeResponse(reqID, nil, map[string]any{
				"code": se.Code, "message": se.Message, "data": se.Data,
			})
		}
		return makeResponse(reqID, nil, map[string]any{
			"code": -32603, "message": "Tool execution failed: " + err.Error(),
		})
	}
	return makeResponse(reqID, map[string]any{"success": true, "tool": tool, "data": data}, nil)
}

func handleMessage(raw json.RawMessage) {
	// Reverse-RPC reply from host → resolve a pending sampling promise.
	if sampling.DispatchResponse(raw) {
		return
	}

	var msg struct {
		ID     any            `json:"id"`
		Method string         `json:"method"`
		Params map[string]any `json:"params"`
	}
	if err := json.Unmarshal(raw, &msg); err != nil {
		_ = writeFrame(makeResponse(nil, nil, map[string]any{"code": -32700, "message": "Parse error"}))
		return
	}
	if msg.Method == "" {
		fmt.Fprintf(os.Stderr, "⚠️  unmatched response id=%v\n", msg.ID)
		return
	}

	var resp map[string]any
	switch msg.Method {
	case "initialize":
		resp = handleInitialize(msg.ID, msg.Params)
	case "describe":
		resp = makeResponse(msg.ID, manifest, nil)
	case "invoke":
		resp = handleInvoke(msg.ID, msg.Params)
	case "health":
		resp = makeResponse(msg.ID, map[string]any{"status": "healthy", "version": manifest["version"]}, nil)
	case "shutdown":
		resp = makeResponse(msg.ID, map[string]any{"ok": true}, nil)
	default:
		resp = makeResponse(msg.ID, nil, map[string]any{"code": -32601, "message": "Method not found: " + msg.Method})
	}
	if msg.ID != nil {
		_ = writeFrame(resp)
	}
}

func main() {
	fmt.Fprintln(os.Stderr, "🔌 sampling-summarizer-go plugin started")
	sampling = sdk.New(writeFrame)

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		// Copy because the scanner reuses its buffer across iterations.
		raw := make([]byte, len(line))
		copy(raw, line)
		go handleMessage(raw)
	}
}
