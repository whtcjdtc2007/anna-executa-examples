package image

import (
	"encoding/json"
	"errors"
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

func (h *fakeHost) reply(c *Client, idx int, result any) {
	raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": h.frames[idx]["id"], "result": result})
	c.DispatchResponse(raw)
}

func (h *fakeHost) replyErr(c *Client, idx int, code int, msg string) {
	raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": h.frames[idx]["id"], "error": map[string]any{"code": code, "message": msg}})
	c.DispatchResponse(raw)
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

func TestGenerateAsyncEnvelopeAndView(t *testing.T) {
	h := &fakeHost{}
	c := New(h.write)
	done := make(chan struct{})
	var view *JobView
	var err error
	go func() {
		view, err = c.GenerateAsync(JobGenerateRequest{Prompt: "a cat", N: 2, Model: "fal-ai/nano-banana-2", Params: map[string]any{"resolution": "2K"}, IdempotencyKey: "inv:1"}, 0)
		close(done)
	}()
	if !h.waitFrames(1) {
		t.Fatal("no frame")
	}
	if h.frames[0]["method"] != MethodImageGenerateAsync {
		t.Fatalf("method %v", h.frames[0]["method"])
	}
	req := h.frames[0]["params"].(JobGenerateRequest)
	if req.Fit != "nearest" || req.N != 2 || req.IdempotencyKey != "inv:1" {
		t.Fatalf("params %+v", req)
	}
	h.reply(c, 0, map[string]any{"jobId": "ijob_x", "kind": "image", "state": "queued", "estimatedCostCU": 48})
	<-done
	if err != nil || view.JobID != "ijob_x" || view.EstimatedCostCU != 48 {
		t.Fatalf("view %+v err %v", view, err)
	}
}

func TestGetCancelResign(t *testing.T) {
	h := &fakeHost{}
	c := New(h.write)
	done := make(chan struct{})
	var res *ResignResult
	go func() {
		_, _ = c.GetJob("ijob_e", 0)
		_, _ = c.CancelJob("ijob_e", 0)
		res, _ = c.Resign([]string{"k1", "k2"}, 0)
		close(done)
	}()
	h.waitFrames(1)
	if h.frames[0]["method"] != MethodImageGetJob {
		t.Fatal("get_job method")
	}
	h.reply(c, 0, map[string]any{"jobId": "ijob_e", "state": "running"})
	h.waitFrames(2)
	if h.frames[1]["method"] != MethodImageCancelJob {
		t.Fatal("cancel_job method")
	}
	h.reply(c, 1, map[string]any{"jobId": "ijob_e", "state": "cancelled", "refunded": true})
	h.waitFrames(3)
	if h.frames[2]["method"] != MethodImageResign {
		t.Fatal("resign method")
	}
	h.reply(c, 2, map[string]any{"urls": []map[string]any{{"r2Key": "k1", "url": "u1", "expiresIn": 1800}, {"r2Key": "k2", "url": "u2", "expiresIn": 1800}}})
	<-done
	if len(res.URLs) != 2 || res.URLs[1].R2Key != "k2" {
		t.Fatalf("resign %+v", res)
	}
}

func TestGenerateAwaitSucceededFailedTimeout(t *testing.T) {
	// succeeded with progress
	h := &fakeHost{}
	c := New(h.write)
	var progress []string
	done := make(chan struct{})
	var view *JobView
	var err error
	go func() {
		view, err = c.GenerateAwait(JobGenerateRequest{Prompt: "x"}, time.Millisecond, time.Second, func(p map[string]any) {
			progress = append(progress, p["phase"].(string))
		})
		close(done)
	}()
	h.waitFrames(1)
	h.reply(c, 0, map[string]any{"jobId": "ijob_w", "state": "queued"})
	h.waitFrames(2)
	h.reply(c, 1, map[string]any{"jobId": "ijob_w", "state": "running", "progress": map[string]any{"phase": "IN_PROGRESS"}})
	h.waitFrames(3)
	h.reply(c, 2, map[string]any{"jobId": "ijob_w", "state": "succeeded", "result": map[string]any{"images": []map[string]any{{"r2Key": "k", "url": "u"}}}})
	<-done
	if err != nil || view.State != "succeeded" || view.Result.Images[0].R2Key != "k" || len(progress) != 1 {
		t.Fatalf("view %+v err %v progress %v", view, err, progress)
	}

	// failed → ImageError(content rejected)
	h = &fakeHost{}
	c = New(h.write)
	done = make(chan struct{})
	go func() {
		_, err = c.GenerateAwait(JobGenerateRequest{Prompt: "x"}, time.Millisecond, time.Second, nil)
		close(done)
	}()
	h.waitFrames(1)
	h.reply(c, 0, map[string]any{"jobId": "ijob_w", "state": "queued"})
	h.waitFrames(2)
	h.reply(c, 1, map[string]any{"jobId": "ijob_w", "state": "failed", "error": map[string]any{"code": "IMAGE_CONTENT_REJECTED", "message": "nsfw"}})
	<-done
	var ie *ImageError
	if !errors.As(err, &ie) || ie.Code != ErrCodeContentRejected {
		t.Fatalf("err %v", err)
	}

	// timeout → JobTimeoutError
	h = &fakeHost{}
	c = New(h.write)
	done = make(chan struct{})
	go func() {
		_, err = c.GenerateAwait(JobGenerateRequest{Prompt: "x"}, 5*time.Millisecond, 20*time.Millisecond, nil)
		close(done)
	}()
	h.waitFrames(1)
	h.reply(c, 0, map[string]any{"jobId": "ijob_w", "state": "queued"})
	for i := 1; i < 10; i++ {
		if !h.waitFrames(i + 1) {
			break
		}
		h.reply(c, i, map[string]any{"jobId": "ijob_w", "state": "running"})
		select {
		case <-done:
			i = 10
		default:
		}
	}
	<-done
	var te *JobTimeoutError
	if !errors.As(err, &te) || te.JobID != "ijob_w" {
		t.Fatalf("err %v", err)
	}
}

func TestErrorEnvelope(t *testing.T) {
	h := &fakeHost{}
	c := New(h.write)
	done := make(chan struct{})
	var err error
	go func() {
		_, err = c.GetJob("mjob_other", 0)
		close(done)
	}()
	h.waitFrames(1)
	h.replyErr(c, 0, ErrCodeJobNotFound, "job not found")
	<-done
	var ie *ImageError
	if !errors.As(err, &ie) || ie.Code != ErrCodeJobNotFound {
		t.Fatalf("err %v", err)
	}
}
