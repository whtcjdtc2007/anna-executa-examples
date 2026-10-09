// Package media implements the Executa v2 reverse JSON-RPC
// `video/generate` / `video/get_job` / `video/cancel_job` and
// `audio/speak` requests that let a plugin ask its host (Anna) to
// generate video / synthesize speech on its behalf.
// Design: matrix-nexus docs/design/platform-media-generation-video-audio.md §11.2.
//
// Why reverse RPC?
//   - Plugins do NOT need their own video/TTS provider API key.
//   - Quota and grant gating live in the host (media_grant on
//     UserExecuta.custom_config + manifest host_capabilities
//     llm.video / llm.audio.speak).
//
// Video generation is an ASYNC JOB: Generate returns a jobId immediately;
// poll GetJob for the terminal state (or use GenerateAwait which polls
// for you). Costs are pre-charged from the estimate and fully refunded
// on failure / cancel / expiry. Speak is synchronous TTS.
//
// Wire layout (Plugin → Agent → Nexus REST):
//
//	Plugin (us)                              Agent (host)              Nexus
//	────────────────────────────────────────────────────────────────────────
//	← invoke(req_id=42, …)
//	→ video/generate(req_id=A, …)            POST /copilot/media/video/generate
//	                                          ← 200 {jobId, state:"queued", …}
//	→ video/get_job(req_id=B, …)             POST /copilot/media/video/get_job
//	                                          ← 200 {state:"succeeded", result:{url…}}
//	← result | error
//	→ invoke result(req_id=42)
//
// Threading model identical to the image client. Construct one *Client
// per process; feed every parsed frame to DispatchResponse.
package media

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

// Constants — keep in sync with matrix/src/executa/protocol.py and
// matrix-nexus/src/services/app_media_facade.py.
const (
	MethodVideoGenerate  = "video/generate"
	MethodVideoGetJob    = "video/get_job"
	MethodVideoCancelJob = "video/cancel_job"
	MethodAudioSpeak     = "audio/speak"

	ErrCodeVideoNotGranted          = -32120
	ErrCodeVideoQuotaExceeded       = -32121
	ErrCodeVideoInvalidRequest      = -32122
	ErrCodeVideoProviderError       = -32123
	ErrCodeVideoModelUnavailable    = -32124
	ErrCodeVideoJobNotFound         = -32125
	ErrCodeVideoJobNotCancellable   = -32126
	ErrCodeVideoConcurrencyExceeded = -32127
	ErrCodeVideoContentRejected     = -32128
	ErrCodeAudioNotGranted          = -32140
	ErrCodeAudioQuotaExceeded       = -32141
	ErrCodeAudioTextTooLong         = -32142
	ErrCodeAudioTooLong             = -32143
	ErrCodeAudioProviderError       = -32144
	ErrCodeAudioVoiceInvalid        = -32145
	ErrCodeAudioModelUnavailable    = -32146
	ErrCodeAudioContentRejected     = -32147
)

// MediaError wraps a JSON-RPC error returned by the host.
type MediaError struct {
	Code    int            `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data,omitempty"`
}

func (e *MediaError) Error() string {
	return fmt.Sprintf("[%d] %s", e.Code, e.Message)
}

// GenerateRequest mirrors `video/generate` params. With ImageURL the clip
// is driven by that image (image-to-video; AspectRatio then follows it).
type GenerateRequest struct {
	Prompt        string `json:"prompt"`
	ImageURL      string `json:"imageUrl,omitempty"`
	DurationSec   int    `json:"durationSec,omitempty"`
	Resolution    string `json:"resolution,omitempty"`
	AspectRatio   string `json:"aspectRatio,omitempty"`
	GenerateAudio *bool  `json:"generateAudio,omitempty"`
	Model         string `json:"model,omitempty"`
	ClientTag     string `json:"clientTag,omitempty"`
}

// SpeakRequest mirrors `audio/speak` params (synchronous TTS).
type SpeakRequest struct {
	Text       string  `json:"text"`
	Voice      string  `json:"voice,omitempty"`
	Language   string  `json:"language,omitempty"`
	Timestamps bool    `json:"timestamps,omitempty"`
	Format     string  `json:"format,omitempty"`   // "mp3"|"wav"
	Speed      float64 `json:"speed,omitempty"`    // 0.5–2.0
	Delivery   string  `json:"delivery,omitempty"` // "url"(default)|"inline"
	Model      string  `json:"model,omitempty"`
}

// VideoResult is the terminal `result` object of a succeeded job.
type VideoResult struct {
	URL         string `json:"url"`
	MimeType    string `json:"mimeType,omitempty"`
	DurationSec int    `json:"durationSec,omitempty"`
	Width       int    `json:"width,omitempty"`
	Height      int    `json:"height,omitempty"`
	Seed        int64  `json:"seed,omitempty"`
	ExpiresIn   int    `json:"expiresIn,omitempty"`
}

// JobView is the response shape of video/generate (partial) and
// video/get_job (full). On succeeded, Result.URL is a freshly re-signed
// presigned GET (~30 min TTL) — call GetJob again after expiry.
type JobView struct {
	JobID           string         `json:"jobId"`
	State           string         `json:"state"`
	EstimatedCostCU int            `json:"estimatedCostCU,omitempty"`
	BilledCostCU    int            `json:"billedCostCU,omitempty"`
	DeadlineAt      string         `json:"deadlineAt,omitempty"`
	ClientTag       string         `json:"clientTag,omitempty"`
	Model           string         `json:"model,omitempty"`
	Progress        map[string]any `json:"progress,omitempty"`
	Result          *VideoResult   `json:"result,omitempty"`
	Error           map[string]any `json:"error,omitempty"`
	Refunded        bool           `json:"refunded,omitempty"`
	CancelPending   *bool          `json:"cancelPending,omitempty"`
}

// SpeakResult is the response of audio/speak.
type SpeakResult struct {
	URL          string `json:"url,omitempty"`
	AudioBase64  string `json:"audioBase64,omitempty"`
	R2Key        string `json:"r2Key,omitempty"`
	MimeType     string `json:"mimeType"`
	CharCount    int    `json:"charCount"`
	BilledCostCU int    `json:"billedCostCU"`
	ExpiresIn    int    `json:"expiresIn,omitempty"`
	Model        string `json:"model,omitempty"` // resolved TTS model
	Voice        string `json:"voice,omitempty"` // resolved voice id
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

// Disable marks the media namespace as unavailable (host did not
// negotiate llm.video / llm.audio.speak).
func (c *Client) Disable(reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.disabledReason = reason
}

// Generate submits an async video generation job; the returned JobView
// carries the jobId to poll with GetJob. Pass timeout = 0 to use the
// default (120s).
func (c *Client) Generate(req GenerateRequest, timeout time.Duration) (*JobView, error) {
	if req.Prompt == "" {
		return nil, errors.New("prompt must be non-empty")
	}
	var out JobView
	if err := c.call(MethodVideoGenerate, req, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetJob fetches the authoritative job snapshot (re-signs result.url).
func (c *Client) GetJob(jobID string, timeout time.Duration) (*JobView, error) {
	if jobID == "" {
		return nil, errors.New("jobID must be non-empty")
	}
	var out JobView
	if err := c.call(MethodVideoGetJob, map[string]any{"jobId": jobID}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CancelJob cancels a job. Queued = guaranteed cancel + full refund;
// running cancel is best-effort (provider may refuse).
func (c *Client) CancelJob(jobID string, timeout time.Duration) (*JobView, error) {
	if jobID == "" {
		return nil, errors.New("jobID must be non-empty")
	}
	var out JobView
	if err := c.call(MethodVideoCancelJob, map[string]any{"jobId": jobID}, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GenerateAwait submits and polls until the job reaches a terminal
// state. pollInterval/awaitTimeout = 0 use the defaults (5s / 9min).
// On await timeout the job keeps rendering server-side; the returned
// error wraps the jobId in Data["jobId"] for later recovery via GetJob.
func (c *Client) GenerateAwait(
	req GenerateRequest,
	pollInterval, awaitTimeout time.Duration,
) (*JobView, error) {
	if pollInterval <= 0 {
		pollInterval = 5 * time.Second
	}
	if awaitTimeout <= 0 {
		awaitTimeout = 9 * time.Minute
	}
	created, err := c.Generate(req, 0)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(awaitTimeout)
	for time.Now().Before(deadline) {
		time.Sleep(pollInterval)
		view, err := c.GetJob(created.JobID, 0)
		if err != nil {
			return nil, err
		}
		switch view.State {
		case "succeeded":
			return view, nil
		case "failed", "cancelled", "expired":
			msg := view.State
			if view.Error != nil {
				if m, ok := view.Error["message"].(string); ok && m != "" {
					msg = fmt.Sprintf("%s: %s", view.State, m)
				}
			}
			return nil, &MediaError{
				Code:    ErrCodeVideoProviderError,
				Message: fmt.Sprintf("video job %s ended as %s", created.JobID, msg),
				Data:    map[string]any{"jobId": created.JobID, "state": view.State},
			}
		}
	}
	return nil, &MediaError{
		Code:    ErrCodeVideoProviderError,
		Message: fmt.Sprintf("video job %s still rendering after %s", created.JobID, awaitTimeout),
		Data:    map[string]any{"jobId": created.JobID, "state": "running"},
	}
}

// Speak issues a synchronous `audio/speak` TTS request
// (EXECUTA_TTS billing). -32145 carries valid voices in Data["allowed"].
func (c *Client) Speak(req SpeakRequest, timeout time.Duration) (*SpeakResult, error) {
	if req.Text == "" {
		return nil, errors.New("text must be non-empty")
	}
	var out SpeakResult
	if err := c.call(MethodAudioSpeak, req, timeout, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) call(method string, params any, timeout time.Duration, out any) error {
	c.mu.Lock()
	if c.disabledReason != "" {
		reason := c.disabledReason
		c.mu.Unlock()
		code := ErrCodeVideoNotGranted
		if method == MethodAudioSpeak {
			code = ErrCodeAudioNotGranted
		}
		return &MediaError{Code: code, Message: reason}
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
		var resp struct {
			Result json.RawMessage `json:"result"`
			Error  *MediaError     `json:"error"`
		}
		if err := json.Unmarshal(raw, &resp); err != nil {
			return fmt.Errorf("decode media response: %w", err)
		}
		if resp.Error != nil {
			return resp.Error
		}
		if resp.Result == nil {
			return errors.New("empty media result")
		}
		return json.Unmarshal(resp.Result, out)
	case <-time.After(timeout):
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return &MediaError{
			Code:    ErrCodeVideoProviderError,
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
