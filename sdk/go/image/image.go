// Package image implements the Executa v2 reverse JSON-RPC
// `image/generate` and `image/edit` requests that let a plugin ask its
// host (Anna) to generate or edit images on its behalf.
//
// Why reverse RPC?
//   - Plugins do NOT need their own LLM/image provider API key.
//   - Quota and grant gating live in the host (image_grant on
//     UserExecuta.custom_config).
//
// Wire layout (Plugin → Agent → Nexus REST):
//
//	Plugin (us)                              Agent (host)              Nexus
//	────────────────────────────────────────────────────────────────────────
//	← invoke(req_id=42, …)
//	→ image/generate(req_id=A, …)            POST /copilot/image/generate
//	                                          ← 200 {images, model, quota_used}
//	← result | error
//	→ invoke result(req_id=42)
//
// Threading model identical to the sampling client. Construct one
// *Client per process; feed every parsed frame to DispatchResponse.
package image

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
	MethodImageGenerate = "image/generate"
	MethodImageEdit     = "image/edit"

	ErrCodeNotGranted           = -32101
	ErrCodeQuotaExceeded        = -32102
	ErrCodeProviderError        = -32103
	ErrCodeInvalidRequest       = -32104
	ErrCodeTimeout              = -32105
	ErrCodeMaxImagesExceeded    = -32106
	ErrCodeNotNegotiated        = -32107
	ErrCodeUserDenied           = -32108
	ErrCodeNoModelAvailable     = -32109
	ErrCodeStorageError         = -32110
	ErrCodeEditNotSupported     = -32311
	ErrCodeMaskUnsupported      = -32312
	ErrCodeNUnsupported         = -32313
	ErrCodeReferenceFetchFailed = -32314
)

// ImageError wraps a JSON-RPC error returned by the host.
type ImageError struct {
	Code    int            `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data,omitempty"`
}

func (e *ImageError) Error() string {
	return fmt.Sprintf("[%d] %s", e.Code, e.Message)
}

// GenerateRequest mirrors `image/generate` params.
type GenerateRequest struct {
	Prompt             string         `json:"prompt"`
	N                  int            `json:"n"`
	Size               string         `json:"size,omitempty"`
	ReferenceImageURLs []string       `json:"reference_image_urls,omitempty"`
	ModelPreferences   map[string]any `json:"modelPreferences,omitempty"`
	Metadata           map[string]any `json:"metadata,omitempty"`
}

// EditRequest mirrors `image/edit` params.
type EditRequest struct {
	ImageURL         string         `json:"image_url"`
	Prompt           string         `json:"prompt"`
	MaskURL          string         `json:"mask_url,omitempty"`
	N                int            `json:"n"`
	Size             string         `json:"size,omitempty"`
	ModelPreferences map[string]any `json:"modelPreferences,omitempty"`
	Metadata         map[string]any `json:"metadata,omitempty"`
}

// GeneratedImage is one item in the result.images array.
type GeneratedImage struct {
	URL      string `json:"url"`
	MimeType string `json:"mimeType,omitempty"`
	Width    int    `json:"width,omitempty"`
	Height   int    `json:"height,omitempty"`
}

// Result is the parsed response from `image/generate` and `image/edit`.
type Result struct {
	Images    []GeneratedImage `json:"images"`
	Model     string           `json:"model,omitempty"`
	QuotaUsed map[string]any   `json:"quota_used,omitempty"`
	Meta      map[string]any   `json:"_meta,omitempty"`
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
	write          FrameWriter
	mu             sync.Mutex
	pending        map[string]*pending
	disabledReason string
	defaultTimeout time.Duration
}

// New constructs a Client. Pass nil to use the default stdout writer.
func New(w FrameWriter) *Client {
	if w == nil {
		w = DefaultFrameWriter()
	}
	return &Client{
		write:          w,
		pending:        map[string]*pending{},
		defaultTimeout: 120 * time.Second,
	}
}

// Disable marks image generation as unavailable.
func (c *Client) Disable(reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.disabledReason = reason
}

// Generate issues an `image/generate` request. Pass timeout = 0 to use
// the default (120s).
func (c *Client) Generate(req GenerateRequest, timeout time.Duration) (*Result, error) {
	if req.Prompt == "" {
		return nil, errors.New("prompt must be non-empty")
	}
	if req.N <= 0 {
		req.N = 1
	}
	return c.call(MethodImageGenerate, req, timeout)
}

// Edit issues an `image/edit` request.
func (c *Client) Edit(req EditRequest, timeout time.Duration) (*Result, error) {
	if req.ImageURL == "" {
		return nil, errors.New("image_url must be non-empty")
	}
	if req.Prompt == "" {
		return nil, errors.New("prompt must be non-empty")
	}
	if req.N <= 0 {
		req.N = 1
	}
	return c.call(MethodImageEdit, req, timeout)
}

func (c *Client) call(method string, params any, timeout time.Duration) (*Result, error) {
	c.mu.Lock()
	if c.disabledReason != "" {
		reason := c.disabledReason
		c.mu.Unlock()
		return nil, &ImageError{Code: ErrCodeNotGranted, Message: reason}
	}
	c.mu.Unlock()
	if timeout <= 0 {
		timeout = c.defaultTimeout
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
		"method":  method,
		"params":  params,
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
			Result *Result     `json:"result"`
			Error  *ImageError `json:"error"`
		}
		if err := json.Unmarshal(raw, &resp); err != nil {
			return nil, fmt.Errorf("decode image response: %w", err)
		}
		if resp.Error != nil {
			return nil, resp.Error
		}
		if resp.Result == nil {
			return nil, errors.New("empty image result")
		}
		return resp.Result, nil
	case <-time.After(timeout):
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return nil, &ImageError{
			Code:    ErrCodeTimeout,
			Message: fmt.Sprintf("%s timed out after %s", method, timeout),
		}
	}
}

// DispatchResponse resolves a pending request from a parsed JSON-RPC
// frame. Returns true if `frame` was a response we owned.
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
