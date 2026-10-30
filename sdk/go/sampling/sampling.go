// Package sampling implements the Executa v2 reverse JSON-RPC
// `sampling/createMessage` request that lets a long-running plugin ask
// its host (Anna) to perform an LLM completion on its behalf.
//
// Why reverse RPC?
//   - Plugins do NOT need their own LLM API key — billing/quotas/model
//     routing are owned by the host.
//   - Plugins describe a desired model via ModelPreferences (MCP shape)
//     and let the host pick a concrete model based on the user's saved
//     preferences.
//
// Wire layout (Executa v2):
//
//	Plugin (us)                              Agent (host)
//	──────────────────────────────────────────────────────────
//	← invoke(req_id=42, …)
//	→ sampling/createMessage(req_id=A)
//	← result | error
//	→ invoke result(req_id=42)
//
// Threading model: construct one *Client per process; the same stdin
// reader feeds both agent-initiated requests AND host responses to our
// reverse calls. Use Client.DispatchResponse on every parsed frame; it
// returns true if the frame was a response we own.
package sampling

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"
)

// Constants — keep in sync with matrix/src/executa/protocol.py.
const (
	ProtocolVersionV1 = "1.1"
	ProtocolVersionV2 = "2.0"

	MethodInitialize            = "initialize"
	MethodShutdown              = "shutdown"
	MethodSamplingCreateMessage = "sampling/createMessage"

	ErrCodeNotGranted                = -32001
	ErrCodeQuotaExceeded             = -32002
	ErrCodeProviderError             = -32003
	ErrCodeInvalidRequest            = -32004
	ErrCodeTimeout                   = -32005
	ErrCodeMaxCallsExceeded          = -32006
	ErrCodeMaxTokensExceeded         = -32007
	ErrCodeNotNegotiated             = -32008
	ErrCodeUserDenied                = -32009
	ErrCodeUnsupportedResponseFormat = -32010
)

// SamplingError wraps a JSON-RPC error returned by the host.
type SamplingError struct {
	Code    int            `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data,omitempty"`
}

func (e *SamplingError) Error() string {
	return fmt.Sprintf("[%d] %s", e.Code, e.Message)
}

// MessageContent is the {"type":"text","text":"..."} block.
type MessageContent struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
}

// Message is one MCP-shaped sampling message.
type Message struct {
	Role    string         `json:"role"` // "user" | "assistant" | "system"
	Content MessageContent `json:"content"`
}

// ModelHint matches MCP's modelPreferences.hints[*].
type ModelHint struct {
	Name string `json:"name,omitempty"`
}

// ModelPreferences maps to MCP modelPreferences.
type ModelPreferences struct {
	Hints                []ModelHint `json:"hints,omitempty"`
	CostPriority         *float64    `json:"costPriority,omitempty"`
	SpeedPriority        *float64    `json:"speedPriority,omitempty"`
	IntelligencePriority *float64    `json:"intelligencePriority,omitempty"`
}

// JSONSchemaSpec is the OpenAI-style `{name, strict?, schema}` block used
// by ResponseFormat type "json_schema". Host-enforced hard limits: schema
// ≤32KB serialized, nesting depth ≤8, ≤512 nodes, name `^[a-zA-Z0-9_-]{1,64}$`.
type JSONSchemaSpec struct {
	Name   string         `json:"name"`
	Strict *bool          `json:"strict,omitempty"` // host defaults to true
	Schema map[string]any `json:"schema"`
}

// ResponseFormat is the optional structured-output constraint.
// Type is "json_object" (L1 — JSON mode, broadly compatible) or
// "json_schema" (L2 — strict schema; requires model support, otherwise the
// host rejects with ErrCodeUnsupportedResponseFormat unless OnUnsupported
// opts into a downgrade). The reply is still Content.Text (a string) —
// parse it yourself; the host adds `_meta.responseFormat.structuredValid`.
type ResponseFormat struct {
	Type       string          `json:"type"` // "json_object" | "json_schema"
	JSONSchema *JSONSchemaSpec `json:"json_schema,omitempty"`
}

// Thinking is the optional thinking / reasoning control for
// reasoning-capable models. Set CreateMessageRequest.Thinking to a plain
// bool (`true` / `false`) or to a *Thinking object. Enabled defaults to true when Effort
// or BudgetTokens is set; BudgetTokens wins over Effort on providers where
// the two are mutually exclusive; IncludeReasoning asks the host to return
// the reasoning text in `_meta.thinking.reasoning`.
//
// Platform default for sampling is thinking OFF when the model can disable
// it — reasoning tokens are invisible to the caller but billed and counted
// against MaxTokens (DashScope/Qwen). Mandatory reasoning models cannot be
// disabled (`_meta.thinking.reason == "mandatory"`). The reply echoes
// `_meta.thinking = {requested, effective:{enabled, effort?, budgetTokens?},
// applied, reason, warning?, reasoning?, reasoningTruncated?}` and
// Usage.ReasoningTokens (only when the provider reports it). Truncation
// check: StopReason == "maxTokens" && OutputTokens - ReasoningTokens <= 0 ⇒
// reasoning ate the budget → retry with thinking disabled or a larger
// MaxTokens.
type Thinking struct {
	Enabled          *bool  `json:"enabled,omitempty"`
	Effort           string `json:"effort,omitempty"` // "minimal"|"low"|"medium"|"high"|"xhigh"|"max"
	BudgetTokens     int    `json:"budgetTokens,omitempty"`
	IncludeReasoning *bool  `json:"includeReasoning,omitempty"`
}

// CreateMessageRequest mirrors the params of `sampling/createMessage`.
type CreateMessageRequest struct {
	Messages         []Message         `json:"messages"`
	MaxTokens        int               `json:"maxTokens"`
	SystemPrompt     string            `json:"systemPrompt,omitempty"`
	Temperature      *float64          `json:"temperature,omitempty"`
	StopSequences    []string          `json:"stopSequences,omitempty"`
	ModelPreferences *ModelPreferences `json:"modelPreferences,omitempty"`
	IncludeContext   string            `json:"includeContext,omitempty"` // "none" only in v1
	Metadata         map[string]string `json:"metadata,omitempty"`
	ResponseFormat   *ResponseFormat   `json:"responseFormat,omitempty"`
	// OnUnsupported: "error" (default), "json_object" (downgrade to L1) or
	// "text" (drop the constraint) when the selected model lacks the
	// requested ResponseFormat level.
	OnUnsupported string `json:"onUnsupported,omitempty"`
	// Thinking: nil (host default — OFF for sampling), a bool (`false` /
	// `true`) or *Thinking. Forwarded verbatim.
	Thinking any `json:"thinking,omitempty"`
}

// Usage mirrors the MCP usage block.
type Usage struct {
	InputTokens  int `json:"inputTokens"`
	OutputTokens int `json:"outputTokens"`
	TotalTokens  int `json:"totalTokens"`
	// ReasoningTokens is present only when the provider reports it.
	ReasoningTokens int `json:"reasoningTokens,omitempty"`
}

// CreateMessageResult is the parsed result returned by the host.
type CreateMessageResult struct {
	Role       string         `json:"role"`
	Content    MessageContent `json:"content"`
	Model      string         `json:"model"`
	StopReason string         `json:"stopReason"`
	Usage      Usage          `json:"usage"`
	Meta       map[string]any `json:"_meta,omitempty"`
}

// FrameWriter writes one newline-delimited JSON-RPC frame to the host.
type FrameWriter func(msg map[string]any) error

// DefaultFrameWriter writes to os.Stdout under a process-wide mutex.
func DefaultFrameWriter() FrameWriter {
	var mu sync.Mutex
	return func(msg map[string]any) error {
		buf, err := json.Marshal(msg)
		if err != nil {
			return err
		}
		mu.Lock()
		defer mu.Unlock()
		if _, err := os.Stdout.Write(append(buf, '\n')); err != nil {
			return err
		}
		return nil
	}
}

type pending struct {
	ch chan json.RawMessage
}

// Client tracks outstanding reverse RPC requests and resolves them as
// responses arrive on stdin.
type Client struct {
	write           FrameWriter
	mu              sync.Mutex
	pending         map[string]*pending
	disabledReason  string
	defaultTimeoutS int
}

// New constructs a Client. Pass nil to use the default stdout writer.
func New(w FrameWriter) *Client {
	if w == nil {
		w = DefaultFrameWriter()
	}
	return &Client{
		write:           w,
		pending:         map[string]*pending{},
		defaultTimeoutS: 90,
	}
}

// Disable marks sampling as unavailable (e.g. host did not negotiate v2).
func (c *Client) Disable(reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.disabledReason = reason
}

// CreateMessage issues a `sampling/createMessage` request and waits for the
// host's response. Pass timeout = 0 to use the default (90s).
func (c *Client) CreateMessage(req CreateMessageRequest, timeout time.Duration) (*CreateMessageResult, error) {
	c.mu.Lock()
	if c.disabledReason != "" {
		reason := c.disabledReason
		c.mu.Unlock()
		return nil, &SamplingError{Code: ErrCodeNotNegotiated, Message: reason}
	}
	c.mu.Unlock()

	if len(req.Messages) == 0 {
		return nil, errors.New("messages must be non-empty")
	}
	if req.MaxTokens <= 0 {
		return nil, errors.New("maxTokens must be > 0")
	}
	if req.IncludeContext == "" {
		req.IncludeContext = "none"
	}
	if timeout <= 0 {
		timeout = time.Duration(c.defaultTimeoutS) * time.Second
	}

	id, err := newReqID()
	if err != nil {
		return nil, err
	}

	p := &pending{ch: make(chan json.RawMessage, 1)}
	c.mu.Lock()
	c.pending[id] = p
	c.mu.Unlock()

	envelope := map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  MethodSamplingCreateMessage,
		"params":  req,
	}
	if err := c.write(envelope); err != nil {
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return nil, err
	}

	select {
	case raw := <-p.ch:
		var resp struct {
			Result *CreateMessageResult `json:"result"`
			Error  *SamplingError       `json:"error"`
		}
		if err := json.Unmarshal(raw, &resp); err != nil {
			return nil, fmt.Errorf("decode sampling response: %w", err)
		}
		if resp.Error != nil {
			return nil, resp.Error
		}
		if resp.Result == nil {
			return nil, errors.New("empty sampling result")
		}
		return resp.Result, nil
	case <-time.After(timeout):
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return nil, &SamplingError{
			Code:    ErrCodeTimeout,
			Message: fmt.Sprintf("sampling/createMessage timed out after %s", timeout),
		}
	}
}

// DispatchResponse resolves the matching pending request from a parsed
// JSON-RPC frame. Returns true if `frame` was a response we owned.
//
// Caller is responsible for parsing the frame once and inspecting whether
// it has a "method" field; if it does, dispatch to your normal handler
// instead of calling this.
func (c *Client) DispatchResponse(frame json.RawMessage) bool {
	var head struct {
		ID     any  `json:"id"`
		Method *any `json:"method"`
	}
	if err := json.Unmarshal(frame, &head); err != nil {
		return false
	}
	if head.Method != nil {
		return false
	}
	idStr, ok := head.ID.(string)
	if !ok {
		return false
	}
	c.mu.Lock()
	p := c.pending[idStr]
	if p != nil {
		delete(c.pending, idStr)
	}
	c.mu.Unlock()
	if p == nil {
		return false
	}
	select {
	case p.ch <- frame:
	default:
	}
	return true
}

func newReqID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}
