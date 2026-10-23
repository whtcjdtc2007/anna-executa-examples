// Package image implements the Executa v2 reverse JSON-RPC
// `image/generate` and `image/edit` requests (synchronous) plus the async
// job family `image/{generate_async, edit_async, get_job, cancel_job, resign}`
// (matrix-nexus docs/design/platform-media-generation-image-jobs.md §11) that
// let a plugin ask its host (Anna) to generate or edit images on its behalf.
//
// Async jobs (GenerateAsync / EditAsync → `ijob_…`) mirror the music job
// surface: submit returns at once, poll GetJob (or use GenerateAwait /
// EditAwait), result.images[].url is re-signed on every GetJob, and Resign
// re-signs any already-paid artifact by r2Key for up to 7 days.
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
	// Async jobs + re-sign (image-jobs §11)
	MethodImageGenerateAsync = "image/generate_async"
	MethodImageEditAsync     = "image/edit_async"
	MethodImageGetJob        = "image/get_job"
	MethodImageCancelJob     = "image/cancel_job"
	MethodImageResign        = "image/resign"

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
	ErrCodeJobNotFound          = -32113
	ErrCodeJobNotCancellable    = -32114
	ErrCodeContentRejected      = -32115
	ErrCodeJobExpired           = -32116
	ErrCodeAssetNotFound        = -32117
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
//
// Quality / Resolution / OutputFormat / EnableWebSearch / ThinkingLevel are
// advanced provider options (host validates; unsupported models ignore
// them): Quality = low|medium|high (GPT-Image family, affects cost);
// Resolution = 0.5K|1K|2K|4K (Nano-Banana family, 0.75x/1x/1.5x/2x rate);
// EnableWebSearch / ThinkingLevel(high) add a small surcharge on
// Nano Banana 2.
type GenerateRequest struct {
	Prompt             string         `json:"prompt"`
	N                  int            `json:"n"`
	Size               string         `json:"size,omitempty"`
	ReferenceImageURLs []string       `json:"reference_image_urls,omitempty"`
	Quality            string         `json:"quality,omitempty"`
	Resolution         string         `json:"resolution,omitempty"`
	OutputFormat       string         `json:"output_format,omitempty"`
	EnableWebSearch    bool           `json:"enable_web_search,omitempty"`
	ThinkingLevel      string         `json:"thinking_level,omitempty"`
	ModelPreferences   map[string]any `json:"modelPreferences,omitempty"`
	Metadata           map[string]any `json:"metadata,omitempty"`
}

// EditRequest mirrors `image/edit` params. MaskURL requires a
// mask-capable model (e.g. GPT Image 2) — otherwise the host rejects
// with -32312.
type EditRequest struct {
	ImageURL         string         `json:"image_url"`
	Prompt           string         `json:"prompt"`
	MaskURL          string         `json:"mask_url,omitempty"`
	N                int            `json:"n"`
	Size             string         `json:"size,omitempty"`
	Quality          string         `json:"quality,omitempty"`
	Resolution       string         `json:"resolution,omitempty"`
	OutputFormat     string         `json:"output_format,omitempty"`
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
// JobGenerateRequest is the body of `image/generate_async` (= GenerateRequest
// contract + model / params / fit / aspect_ratio + clientTag / idempotencyKey).
type JobGenerateRequest struct {
	Prompt             string         `json:"prompt"`
	N                  int            `json:"n"`
	Size               string         `json:"size,omitempty"`
	AspectRatio        string         `json:"aspect_ratio,omitempty"`
	Model              string         `json:"model,omitempty"`
	Params             map[string]any `json:"params,omitempty"`
	Fit                string         `json:"fit,omitempty"`
	ReferenceImageURLs []string       `json:"reference_image_urls,omitempty"`
	ClientTag          string         `json:"clientTag,omitempty"`
	IdempotencyKey     string         `json:"idempotencyKey,omitempty"`
}

// JobEditRequest is the body of `image/edit_async`.
type JobEditRequest struct {
	ImageURL       string         `json:"image_url"`
	Prompt         string         `json:"prompt"`
	N              int            `json:"n"`
	Size           string         `json:"size,omitempty"`
	AspectRatio    string         `json:"aspect_ratio,omitempty"`
	Model          string         `json:"model,omitempty"`
	Params         map[string]any `json:"params,omitempty"`
	Fit            string         `json:"fit,omitempty"`
	MaskURL        string         `json:"mask_url,omitempty"`
	ClientTag      string         `json:"clientTag,omitempty"`
	IdempotencyKey string         `json:"idempotencyKey,omitempty"`
}

// JobImage is one artifact in a job view; URL is re-signed per GetJob (~30 min)
// and may be empty once the object has been garbage-collected (7 days).
type JobImage struct {
	URL       string `json:"url,omitempty"`
	R2Key     string `json:"r2Key,omitempty"`
	MimeType  string `json:"mimeType,omitempty"`
	Width     int    `json:"width,omitempty"`
	Height    int    `json:"height,omitempty"`
	ExpiresIn int    `json:"expiresIn,omitempty"`
}

// JobResult is `result` of a succeeded image job.
type JobResult struct {
	Images        []JobImage     `json:"images"`
	RequestedSize string         `json:"requestedSize,omitempty"`
	Size          string         `json:"size,omitempty"`
	SizeAdjusted  bool           `json:"sizeAdjusted,omitempty"`
	AspectRatio   string         `json:"aspectRatio,omitempty"`
	Fit           string         `json:"fit,omitempty"`
	Params        map[string]any `json:"params,omitempty"`
	IgnoredParams []string       `json:"ignoredParams,omitempty"`
}

// JobView is the authoritative job snapshot returned by GenerateAsync /
// EditAsync (state=queued) and GetJob / CancelJob.
type JobView struct {
	JobID            string         `json:"jobId"`
	Kind             string         `json:"kind,omitempty"`
	State            string         `json:"state"`
	ClientTag        string         `json:"clientTag,omitempty"`
	IdempotencyKey   string         `json:"idempotencyKey,omitempty"`
	Request          map[string]any `json:"request,omitempty"`
	Progress         map[string]any `json:"progress,omitempty"`
	Result           *JobResult     `json:"result,omitempty"`
	Error            map[string]any `json:"error,omitempty"`
	EstimatedCostCU  int            `json:"estimatedCostCU,omitempty"`
	BilledCostCU     int            `json:"billedCostCU,omitempty"`
	DeadlineAt       string         `json:"deadlineAt,omitempty"`
	CreatedAt        string         `json:"createdAt,omitempty"`
	UpdatedAt        string         `json:"updatedAt,omitempty"`
	IdempotentReplay bool           `json:"idempotentReplay,omitempty"`
	Refunded         bool           `json:"refunded,omitempty"`
	CancelPending    bool           `json:"cancelPending,omitempty"`
}

// ResignedURL is one entry of the `image/resign` response.
type ResignedURL struct {
	R2Key     string `json:"r2Key"`
	URL       string `json:"url"`
	ExpiresIn int    `json:"expiresIn"`
}

// ResignResult is the `image/resign` response (same order as the input keys).
type ResignResult struct {
	URLs []ResignedURL `json:"urls"`
}

// JobTimeoutError is returned by GenerateAwait / EditAwait when the wall clock
// elapses before the job reaches a terminal state; the job may still be
// rendering — resume with GetJob(JobID).
type JobTimeoutError struct {
	JobID  string
	Waited time.Duration
}

func (e *JobTimeoutError) Error() string {
	return fmt.Sprintf("image job %s still rendering after %s", e.JobID, e.Waited.Round(time.Second))
}

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
	raw, err := c.callRaw(method, params, timeout)
	if err != nil {
		return nil, err
	}
	var out Result
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("decode image response: %w", err)
	}
	return &out, nil
}

// callRaw sends one reverse-RPC request and returns the raw `result` member.
func (c *Client) callRaw(method string, params any, timeout time.Duration) (json.RawMessage, error) {
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
			Result json.RawMessage `json:"result"`
			Error  *ImageError     `json:"error"`
		}
		if err := json.Unmarshal(raw, &resp); err != nil {
			return nil, fmt.Errorf("decode image response: %w", err)
		}
		if resp.Error != nil {
			return nil, resp.Error
		}
		if len(resp.Result) == 0 || string(resp.Result) == "null" {
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

// ---- async jobs + re-sign (image-jobs §11.5) ----

const (
	// DefaultPollInterval / DefaultAwaitTimeout match the music job cadence.
	DefaultPollInterval = 5 * time.Second
	DefaultAwaitTimeout = 9 * time.Minute
	jobControlTimeout   = 60 * time.Second
)

func (c *Client) callJob(method string, params any, timeout time.Duration) (*JobView, error) {
	if timeout <= 0 {
		timeout = jobControlTimeout
	}
	raw, err := c.callRaw(method, params, timeout)
	if err != nil {
		return nil, err
	}
	var view JobView
	if err := json.Unmarshal(raw, &view); err != nil {
		return nil, fmt.Errorf("decode image job view: %w", err)
	}
	return &view, nil
}

// GenerateAsync submits an async text-to-image / reference-image job
// (`image/generate_async`). One job = N images, billed up-front and settled on
// actual output. Set IdempotencyKey (e.g. invokeID+":"+attempt) so retries never
// double-charge. Pass timeout = 0 for the default (60s).
func (c *Client) GenerateAsync(req JobGenerateRequest, timeout time.Duration) (*JobView, error) {
	if req.Prompt == "" {
		return nil, errors.New("prompt must be non-empty")
	}
	if req.N <= 0 {
		req.N = 1
	}
	if req.Fit == "" {
		req.Fit = "nearest"
	}
	return c.callJob(MethodImageGenerateAsync, req, timeout)
}

// EditAsync submits an async edit job (`image/edit_async`; optional mask —
// -32312 when the resolved model cannot mask).
func (c *Client) EditAsync(req JobEditRequest, timeout time.Duration) (*JobView, error) {
	if req.ImageURL == "" {
		return nil, errors.New("image_url must be non-empty")
	}
	if req.Prompt == "" {
		return nil, errors.New("prompt must be non-empty")
	}
	if req.N <= 0 {
		req.N = 1
	}
	if req.Fit == "" {
		req.Fit = "nearest"
	}
	return c.callJob(MethodImageEditAsync, req, timeout)
}

// GetJob returns the authoritative job view; Result.Images[].URL is re-signed on
// every call (~30 min). Only jobs submitted by this Executa are visible.
func (c *Client) GetJob(jobID string, timeout time.Duration) (*JobView, error) {
	return c.callJob(MethodImageGetJob, map[string]any{"jobId": jobID}, timeout)
}

// CancelJob cancels a job: queued = guaranteed + full refund; running =
// best-effort (the provider may refuse → job keeps running, no refund).
func (c *Client) CancelJob(jobID string, timeout time.Duration) (*JobView, error) {
	return c.callJob(MethodImageCancelJob, map[string]any{"jobId": jobID}, timeout)
}

// Resign re-signs already-paid artifacts by r2Key (1–20). URLs come back in
// input order; a key that is not yours fails the whole call with -32117.
func (c *Client) Resign(r2Keys []string, timeout time.Duration) (*ResignResult, error) {
	if len(r2Keys) == 0 {
		return nil, errors.New("r2Keys must be non-empty")
	}
	if timeout <= 0 {
		timeout = jobControlTimeout
	}
	raw, err := c.callRaw(MethodImageResign, map[string]any{"r2Keys": r2Keys}, timeout)
	if err != nil {
		return nil, err
	}
	var out ResignResult
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("decode image resign result: %w", err)
	}
	return &out, nil
}

func (c *Client) awaitJob(
	created *JobView,
	pollInterval, awaitTimeout time.Duration,
	onProgress func(map[string]any),
) (*JobView, error) {
	if pollInterval <= 0 {
		pollInterval = DefaultPollInterval
	}
	if awaitTimeout <= 0 {
		awaitTimeout = DefaultAwaitTimeout
	}
	terminal := func(view *JobView) (*JobView, error, bool) {
		switch view.State {
		case "succeeded":
			return view, nil, true
		case "failed", "cancelled", "expired":
			code := ErrCodeProviderError
			msg := view.State
			if view.Error != nil {
				if cname, ok := view.Error["code"].(string); ok {
					switch cname {
					case "IMAGE_CONTENT_REJECTED":
						code = ErrCodeContentRejected
					case "IMAGE_JOB_EXPIRED":
						code = ErrCodeJobExpired
					}
				}
				if m, ok := view.Error["message"].(string); ok && m != "" {
					msg = fmt.Sprintf("%s: %s", view.State, m)
				}
			}
			return nil, &ImageError{
				Code:    code,
				Message: fmt.Sprintf("image job %s ended as %s", created.JobID, msg),
				Data:    map[string]any{"jobId": created.JobID, "state": view.State, "error": view.Error},
			}, true
		}
		return nil, nil, false
	}
	if v, e, done := terminal(created); done {
		return v, e
	}
	start := time.Now()
	deadline := start.Add(awaitTimeout)
	for time.Now().Before(deadline) {
		time.Sleep(pollInterval)
		view, err := c.GetJob(created.JobID, 0)
		if err != nil {
			return nil, err
		}
		if onProgress != nil && view.Progress != nil {
			onProgress(view.Progress)
		}
		if v, e, done := terminal(view); done {
			return v, e
		}
	}
	return nil, &JobTimeoutError{JobID: created.JobID, Waited: time.Since(start)}
}

// GenerateAwait = GenerateAsync + poll until terminal (pure polling; same
// cadence as MusicGenerateAwait: 5 s / 9 min; pass 0 for defaults). Returns
// the terminal job view; *ImageError on failed / cancelled / expired and
// *JobTimeoutError (carrying JobID) when the wall clock elapses first. Keep
// awaitTimeout within the invoke budget — for longer renders return JobID as
// the invoke result and resume with GetJob. onProgress may be nil.
func (c *Client) GenerateAwait(
	req JobGenerateRequest,
	pollInterval, awaitTimeout time.Duration,
	onProgress func(map[string]any),
) (*JobView, error) {
	created, err := c.GenerateAsync(req, 0)
	if err != nil {
		return nil, err
	}
	return c.awaitJob(created, pollInterval, awaitTimeout, onProgress)
}

// EditAwait = EditAsync + poll until terminal (see GenerateAwait).
func (c *Client) EditAwait(
	req JobEditRequest,
	pollInterval, awaitTimeout time.Duration,
	onProgress func(map[string]any),
) (*JobView, error) {
	created, err := c.EditAsync(req, 0)
	if err != nil {
		return nil, err
	}
	return c.awaitJob(created, pollInterval, awaitTimeout, onProgress)
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
