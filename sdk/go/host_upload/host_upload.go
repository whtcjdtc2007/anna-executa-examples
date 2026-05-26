// Package hostupload implements the Executa v2 reverse JSON-RPC
// `host/uploadFile` request that lets a plugin upload transient bytes to
// Anna's R2 bucket and receive an HTTPS download URL suitable for
// passing into other host capabilities (image/edit, sampling, …).
//
// Three modes are selected via Mode:
//   - "inline":    base64 payload (≤8MB). Simplest; one round-trip.
//   - "negotiate": host returns a presigned PUT URL; the plugin PUTs
//     bytes directly to R2.
//   - "confirm":   after the PUT completes, ask the host for the
//     transient download URL.
//
// Wire layout (Plugin → Agent → Nexus):
//
//	Plugin (us)                              Agent (host)              Nexus
//	────────────────────────────────────────────────────────────────────────
//	→ host/uploadFile mode=inline           POST /copilot/upload
//	                                          ← 200 {download_url, r2_key, …}
//	← result | error
package hostupload

import (
	"crypto/rand"
	"encoding/base64"
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
	MethodHostUploadFile = "host/uploadFile"

	ErrCodeNotGranted       = -32201
	ErrCodeQuotaExceeded    = -32202
	ErrCodeInvalidRequest   = -32203
	ErrCodeTooLarge         = -32204
	ErrCodeMIMERejected     = -32205
	ErrCodePurposeRejected  = -32206
	ErrCodeStorageError     = -32207
	ErrCodeTimeout          = -32208
	ErrCodeUserDenied       = -32209
	ErrCodeNotNegotiated    = -32210
	ErrCodeMaxFilesExceeded = -32211
	ErrCodeNotFound         = -32212
	ErrCodePresignFailed    = -32213

	// MaxInlineBytes is the SDK-side cap for inline uploads. The host
	// may impose a smaller cap via upload_grant.max_file_bytes.
	MaxInlineBytes = 8 * 1024 * 1024
)

// UploadError wraps a JSON-RPC error returned by the host.
type UploadError struct {
	Code    int            `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data,omitempty"`
}

func (e *UploadError) Error() string {
	return fmt.Sprintf("[%d] %s", e.Code, e.Message)
}

// InlineRequest uploads `Content` (raw bytes) base64-encoded.
type InlineRequest struct {
	Filename string
	MimeType string
	Content  []byte
	Purpose  string
	Metadata map[string]any
}

// NegotiateRequest asks the host for a presigned PUT URL.
type NegotiateRequest struct {
	Filename  string
	MimeType  string
	SizeBytes int64
	Purpose   string
	Metadata  map[string]any
}

// NegotiateResult is the returned presigned-upload info.
type NegotiateResult struct {
	PutURL    string            `json:"put_url"`
	Headers   map[string]string `json:"headers"`
	R2Key     string            `json:"r2_key"`
	ExpiresAt string            `json:"expires_at,omitempty"`
}

// ConfirmResult is the transient download URL after PUT completes.
type ConfirmResult struct {
	DownloadURL string `json:"download_url"`
	R2Key       string `json:"r2_key"`
	SizeBytes   int64  `json:"size_bytes,omitempty"`
	ExpiresAt   string `json:"expires_at,omitempty"`
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

// Client tracks outstanding reverse RPC requests.
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

// Disable marks host upload as unavailable.
func (c *Client) Disable(reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.disabledReason = reason
}

// UploadInline uploads raw bytes base64-encoded. Resolves to
// ConfirmResult-shaped data (download_url, r2_key, expires_at, …).
func (c *Client) UploadInline(req InlineRequest, timeout time.Duration) (*ConfirmResult, error) {
	if req.Filename == "" || req.MimeType == "" {
		return nil, errors.New("filename and mime_type are required")
	}
	if int64(len(req.Content)) > MaxInlineBytes {
		return nil, &UploadError{
			Code:    ErrCodeTooLarge,
			Message: fmt.Sprintf("inline payload %d bytes exceeds SDK cap %d — use Negotiate()", len(req.Content), MaxInlineBytes),
		}
	}
	params := map[string]any{
		"mode":        "inline",
		"filename":    req.Filename,
		"mime_type":   req.MimeType,
		"content_b64": base64.StdEncoding.EncodeToString(req.Content),
	}
	if req.Purpose != "" {
		params["purpose"] = req.Purpose
	}
	if req.Metadata != nil {
		params["metadata"] = req.Metadata
	}
	var out ConfirmResult
	if err := c.call(MethodHostUploadFile, params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Negotiate requests a presigned PUT URL.
func (c *Client) Negotiate(req NegotiateRequest, timeout time.Duration) (*NegotiateResult, error) {
	if req.Filename == "" || req.MimeType == "" {
		return nil, errors.New("filename and mime_type are required")
	}
	params := map[string]any{
		"mode":       "negotiate",
		"filename":   req.Filename,
		"mime_type":  req.MimeType,
		"size_bytes": req.SizeBytes,
	}
	if req.Purpose != "" {
		params["purpose"] = req.Purpose
	}
	if req.Metadata != nil {
		params["metadata"] = req.Metadata
	}
	var out NegotiateResult
	if err := c.call(MethodHostUploadFile, params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Confirm finalises a presigned upload.
func (c *Client) Confirm(r2Key string, timeout time.Duration) (*ConfirmResult, error) {
	if r2Key == "" {
		return nil, errors.New("r2_key required")
	}
	params := map[string]any{"mode": "confirm", "r2_key": r2Key}
	var out ConfirmResult
	if err := c.call(MethodHostUploadFile, params, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) call(method string, params any, timeout time.Duration, out any) error {
	c.mu.Lock()
	if c.disabledReason != "" {
		reason := c.disabledReason
		c.mu.Unlock()
		return &UploadError{Code: ErrCodeNotGranted, Message: reason}
	}
	c.mu.Unlock()
	if timeout <= 0 {
		timeout = c.defaultTimeout
	}

	id, err := newReqID()
	if err != nil {
		return err
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
		return err
	}

	select {
	case raw := <-p.ch:
		var head struct {
			Result json.RawMessage `json:"result"`
			Error  *UploadError    `json:"error"`
		}
		if err := json.Unmarshal(raw, &head); err != nil {
			return fmt.Errorf("decode upload response: %w", err)
		}
		if head.Error != nil {
			return head.Error
		}
		if len(head.Result) == 0 {
			return errors.New("empty upload result")
		}
		return json.Unmarshal(head.Result, out)
	case <-time.After(timeout):
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return &UploadError{
			Code:    ErrCodeTimeout,
			Message: fmt.Sprintf("%s timed out after %s", method, timeout),
		}
	}
}

// DispatchResponse resolves a pending request from a parsed frame.
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
