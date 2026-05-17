// focus-session — Executa stdio tool plugin (Go flavour).
//
// Mirrors ../focus-session-python so the same Anna App bundle can drive
// either runtime. State persists to ~/.anna/focus-flow/state.json (shared
// path with the Python and Node flavours — only one should be enabled at a
// time per the app manifest).
//
// Protocol: JSON-RPC 2.0 over stdio (newline-delimited).
// Methods : describe, invoke, health.
//
// IMPORTANT — tool_id minting:
//
//	`manifestName` below + executa.json `tool_id` MUST equal the tool_id
//	minted at https://anna.partners/executa. The shared
//	../../scripts/set-tool-id.py helper only rewrites Python files; for
//	this Go flavour update the constant + executa.json by hand.
package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Plugin manifest — Anna calls `describe` and uses this map verbatim.
// ---------------------------------------------------------------------------

const (
	manifestName    = "tool-test-focus-session-12345678"
	manifestVersion = "1.0.0"
	maxHistory      = 200
)

func manifest() map[string]any {
	return map[string]any{
		"name":         manifestName,
		"display_name": "Focus Session",
		"version":      manifestVersion,
		"description": "Pomodoro / deep-work session timer. State persists to " +
			"~/.anna/focus-flow/state.json.",
		"author":   "Acme Labs",
		"homepage": "https://github.com/openclaw/anna-executa-examples",
		"license":  "MIT",
		"tags":     []string{"productivity", "focus", "pomodoro", "anna-app"},
		"tools": []map[string]any{
			{
				"name": "session",
				"description": "Manage a focus session. Use the `action` parameter to select " +
					"an operation: start | pause | resume | complete | get_state.",
				"parameters": []map[string]any{
					{"name": "action", "type": "string",
						"description": "One of: start, pause, resume, complete, get_state.",
						"required":    true},
					{"name": "duration_minutes", "type": "integer",
						"description": "Required when action='start'. 1-180 minutes.",
						"required":    false},
					{"name": "topic", "type": "string",
						"description": "Optional label for action='start' (max 120 chars).",
						"required":    false, "default": ""},
					{"name": "notes", "type": "string",
						"description": "Optional reflection for action='complete' (max 500 chars).",
						"required":    false, "default": ""},
				},
			},
		},
		"runtime": map[string]any{"type": "go", "min_version": "1.21"},
	}
}

// ---------------------------------------------------------------------------
// State persistence
// ---------------------------------------------------------------------------

var stateMu sync.Mutex

type sessionRec struct {
	ID                 string   `json:"id"`
	Topic              string   `json:"topic"`
	DurationSeconds    int64    `json:"duration_seconds"`
	StartedAt          float64  `json:"started_at"`
	CompletedAt        float64  `json:"completed_at,omitempty"`
	FocusedSeconds     int64    `json:"focused_seconds"`
	Notes              string   `json:"notes,omitempty"`
	RunningSince       *float64 `json:"running_since,omitempty"`
	AccumulatedSeconds int64    `json:"accumulated_seconds,omitempty"`
	Status             string   `json:"status,omitempty"`
}

type appState struct {
	Active  *sessionRec  `json:"active"`
	History []sessionRec `json:"history"`
}

func stateDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".anna", "focus-flow")
}

func stateFile() string {
	return filepath.Join(stateDir(), "state.json")
}

func nowSec() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func loadState() *appState {
	stateMu.Lock()
	defer stateMu.Unlock()
	path := stateFile()
	body, err := os.ReadFile(path)
	if err != nil {
		return &appState{}
	}
	s := &appState{}
	if err := json.Unmarshal(body, s); err != nil {
		backup := strings.TrimSuffix(path, ".json") +
			fmt.Sprintf(".broken.%d.json", time.Now().Unix())
		_ = os.Rename(path, backup)
		fmt.Fprintf(os.Stderr,
			"[focus-session] corrupt state moved to %s: %v\n", backup, err)
		return &appState{}
	}
	return s
}

func saveState(s *appState) error {
	stateMu.Lock()
	defer stateMu.Unlock()
	if err := os.MkdirAll(stateDir(), 0o755); err != nil {
		return err
	}
	body, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := stateFile() + ".tmp"
	if err := os.WriteFile(tmp, body, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, stateFile())
}

func todayTotals(history []sessionRec) map[string]any {
	t := time.Now()
	dayStart := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, t.Location())
	dayStartSec := float64(dayStart.Unix())
	var seconds int64
	count := 0
	for _, h := range history {
		if h.CompletedAt >= dayStartSec {
			seconds += h.FocusedSeconds
			count++
		}
	}
	mins := math.Round((float64(seconds)/60.0)*10) / 10
	return map[string]any{
		"session_count":   count,
		"focused_minutes": mins,
		"focused_seconds": seconds,
	}
}

func focusedSeconds(a *sessionRec) int64 {
	if a == nil {
		return 0
	}
	acc := a.AccumulatedSeconds
	if a.Status == "running" && a.RunningSince != nil {
		delta := int64(nowSec() - *a.RunningSince)
		if delta > 0 {
			acc += delta
		}
	}
	if acc < 0 {
		return 0
	}
	return acc
}

func activeView(a *sessionRec) map[string]any {
	if a == nil {
		return nil
	}
	focused := focusedSeconds(a)
	remain := a.DurationSeconds - focused
	if remain < 0 {
		remain = 0
	}
	view := map[string]any{
		"id":                  a.ID,
		"topic":               a.Topic,
		"duration_seconds":    a.DurationSeconds,
		"started_at":          a.StartedAt,
		"accumulated_seconds": a.AccumulatedSeconds,
		"status":              a.Status,
		"focused_seconds":     focused,
		"remaining_seconds":   remain,
	}
	if a.RunningSince != nil {
		view["running_since"] = *a.RunningSince
	} else {
		view["running_since"] = nil
	}
	return view
}

func newID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// ---------------------------------------------------------------------------
// Action implementations
// ---------------------------------------------------------------------------

func actionStart(args map[string]any) (any, error) {
	durRaw, ok := args["duration_minutes"]
	if !ok || durRaw == nil {
		return nil, errors.New("duration_minutes is required for action='start'")
	}
	durFloat, ok := toFloat(durRaw)
	if !ok || math.Floor(durFloat) != durFloat {
		return nil, errors.New("duration_minutes must be an integer")
	}
	dm := int64(durFloat)
	if dm < 1 || dm > 180 {
		return nil, errors.New("duration_minutes must be between 1 and 180")
	}
	topic := strings.TrimSpace(strOf(args["topic"]))
	if len(topic) > 120 {
		topic = topic[:120]
	}
	s := loadState()
	t := nowSec()
	rs := t
	s.Active = &sessionRec{
		ID:              newID(),
		Topic:           topic,
		DurationSeconds: dm * 60,
		StartedAt:       t,
		RunningSince:    &rs,
		Status:          "running",
	}
	if err := saveState(s); err != nil {
		return nil, err
	}
	return map[string]any{"active": activeView(s.Active)}, nil
}

func actionPause() (any, error) {
	s := loadState()
	if s.Active == nil {
		return map[string]any{"active": nil, "message": "No active session to pause."}, nil
	}
	if s.Active.Status == "running" {
		s.Active.AccumulatedSeconds = focusedSeconds(s.Active)
		s.Active.Status = "paused"
		s.Active.RunningSince = nil
		if err := saveState(s); err != nil {
			return nil, err
		}
	}
	return map[string]any{"active": activeView(s.Active)}, nil
}

func actionResume() (any, error) {
	s := loadState()
	if s.Active == nil {
		return map[string]any{"active": nil, "message": "No active session to resume."}, nil
	}
	if s.Active.Status != "running" {
		t := nowSec()
		s.Active.RunningSince = &t
		s.Active.Status = "running"
		if err := saveState(s); err != nil {
			return nil, err
		}
	}
	return map[string]any{"active": activeView(s.Active)}, nil
}

func actionComplete(args map[string]any) (any, error) {
	s := loadState()
	if s.Active == nil {
		return map[string]any{"completed": nil, "message": "No active session."}, nil
	}
	notes := strings.TrimSpace(strOf(args["notes"]))
	if len(notes) > 500 {
		notes = notes[:500]
	}
	rec := sessionRec{
		ID:              s.Active.ID,
		Topic:           s.Active.Topic,
		DurationSeconds: s.Active.DurationSeconds,
		FocusedSeconds:  focusedSeconds(s.Active),
		StartedAt:       s.Active.StartedAt,
		CompletedAt:     nowSec(),
		Notes:           notes,
	}
	s.History = append([]sessionRec{rec}, s.History...)
	if len(s.History) > maxHistory {
		s.History = s.History[:maxHistory]
	}
	s.Active = nil
	if err := saveState(s); err != nil {
		return nil, err
	}
	return map[string]any{"completed": rec, "today": todayTotals(s.History)}, nil
}

func actionGetState() (any, error) {
	s := loadState()
	hist := s.History
	if len(hist) > 10 {
		hist = hist[:10]
	}
	return map[string]any{
		"active": activeView(s.Active),
		"today":  todayTotals(s.History),
		"recent": hist,
	}, nil
}

func toolSession(args map[string]any) (any, error) {
	action := strOf(args["action"])
	switch action {
	case "start":
		return actionStart(args)
	case "pause":
		return actionPause()
	case "resume":
		return actionResume()
	case "complete":
		return actionComplete(args)
	case "get_state":
		return actionGetState()
	default:
		return nil, fmt.Errorf("unknown action: %q; expected one of "+
			"start | pause | resume | complete | get_state", action)
	}
}

// ---------------------------------------------------------------------------
// JSON-RPC handlers
// ---------------------------------------------------------------------------

type rpcReq struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      any             `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type rpcErr struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type rpcResp struct {
	JSONRPC string  `json:"jsonrpc"`
	ID      any     `json:"id"`
	Result  any     `json:"result,omitempty"`
	Error   *rpcErr `json:"error,omitempty"`
}

func handleDescribe(_ map[string]any) (any, error) {
	return manifest(), nil
}

func handleInvoke(p map[string]any) (any, error) {
	tool := strOf(p["tool"])
	rawArgs, _ := p["arguments"].(map[string]any)
	if tool != "session" {
		return nil, fmt.Errorf("unknown tool: %q", tool)
	}
	payload, err := toolSession(rawArgs)
	if err != nil {
		return map[string]any{
			"success": false,
			"error":   fmt.Sprintf("%T: %v", err, err),
		}, nil
	}
	return map[string]any{"success": true, "data": payload}, nil
}

func handleHealth(_ map[string]any) (any, error) {
	return map[string]any{"status": "ok", "state_file": stateFile()}, nil
}

var dispatch = map[string]func(map[string]any) (any, error){
	"describe": handleDescribe,
	"invoke":   handleInvoke,
	"health":   handleHealth,
}

// ---------------------------------------------------------------------------
// Stdio loop
// ---------------------------------------------------------------------------

func send(w *bufio.Writer, msg rpcResp) {
	body, _ := json.Marshal(msg)
	body = append(body, '\n')
	_, _ = w.Write(body)
	_ = w.Flush()
}

func main() {
	fmt.Fprintf(os.Stderr,
		"[focus-session] Focus Session v%s ready (go)\n", manifestVersion)
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1024*1024), 16*1024*1024)
	out := bufio.NewWriter(os.Stdout)
	for in.Scan() {
		line := strings.TrimSpace(in.Text())
		if line == "" {
			continue
		}
		var req rpcReq
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			send(out, rpcResp{JSONRPC: "2.0", ID: nil,
				Error: &rpcErr{Code: -32700, Message: "parse error: " + err.Error()}})
			continue
		}
		var params map[string]any
		if len(req.Params) > 0 {
			_ = json.Unmarshal(req.Params, &params)
		}
		if params == nil {
			params = map[string]any{}
		}
		fn, ok := dispatch[req.Method]
		if !ok {
			send(out, rpcResp{JSONRPC: "2.0", ID: req.ID,
				Error: &rpcErr{Code: -32601, Message: "method not found: " + req.Method}})
			continue
		}
		result, err := fn(params)
		if err != nil {
			send(out, rpcResp{JSONRPC: "2.0", ID: req.ID,
				Error: &rpcErr{Code: -32000, Message: err.Error()}})
			continue
		}
		send(out, rpcResp{JSONRPC: "2.0", ID: req.ID, Result: result})
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func strOf(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprintf("%v", v)
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case string:
		// Best effort — JSON-RPC numbers typically arrive as float64.
		var f float64
		_, err := fmt.Sscanf(n, "%f", &f)
		return f, err == nil
	}
	return 0, false
}

// Force-import to avoid "imported and not used" if we trim helpers later.
var _ = sort.Strings
