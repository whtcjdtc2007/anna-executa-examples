/**
 * MediaClient — issue reverse `video/*` and `audio/speak` JSON-RPC requests
 * to the host Agent (Anna), which proxies to Nexus's
 * `/api/v1/copilot/media/*` endpoints using a short-lived `media_token`
 * (aud=executa-media) the host minted at invoke time.
 * Design: matrix-nexus docs/design/platform-media-generation-video-audio.md §11.2.
 *
 * Video generation is an ASYNC JOB: `videoGenerate()` returns a `jobId`
 * immediately; poll `videoGetJob()` for the terminal state (or use the
 * `generateAwait()` sugar which polls for you). Costs are pre-charged from
 * the estimate and fully refunded on failure / cancel / expiry.
 * `speak()` is synchronous TTS.
 *
 * Both capabilities are gated by the user's `media_grant` block on
 * UserExecuta.custom_config AND the manifest `host_capabilities`
 * declaration (`llm.video` / `llm.audio.speak`).
 *
 * Wire protocol (Plugin → Agent → Nexus):
 *   Plugin (us)                       Agent (host)                Nexus
 *   ─────────────────────────────────────────────────────────────────────
 *   ← invoke(req_id=42, …)
 *   → video/generate(req_id=A, …)    → POST /copilot/media/video/generate
 *                                       header: ******
 *                                     ← 200 {jobId, state:"queued", …}
 *   → video/get_job(req_id=B, …)     → POST /copilot/media/video/get_job
 *                                     ← 200 {state:"succeeded", result:{url…}}
 *   ← result | error
 *
 * Threading model identical to SamplingClient / ImageClient:
 *   - Construct one MediaClient per process.
 *   - Feed every parsed JSON-RPC frame received on stdin to
 *     `media.dispatchResponse(msg)`; pair with `makeResponseRouter()`.
 *
 * Error codes — keep in sync with matrix/src/executa/protocol.py:
 *   VIDEO_ERR_NOT_GRANTED          = -32120
 *   VIDEO_ERR_QUOTA_EXCEEDED       = -32121
 *   VIDEO_ERR_INVALID_REQUEST      = -32122
 *   VIDEO_ERR_PROVIDER_ERROR       = -32123
 *   VIDEO_ERR_MODEL_UNAVAILABLE    = -32124
 *   VIDEO_ERR_JOB_NOT_FOUND        = -32125
 *   VIDEO_ERR_JOB_NOT_CANCELLABLE  = -32126
 *   VIDEO_ERR_CONCURRENCY_EXCEEDED = -32127
 *   VIDEO_ERR_CONTENT_REJECTED     = -32128
 *   AUDIO_ERR_NOT_GRANTED          = -32140
 *   AUDIO_ERR_QUOTA_EXCEEDED       = -32141
 *   AUDIO_ERR_TEXT_TOO_LONG        = -32142
 *   AUDIO_ERR_TOO_LONG             = -32143
 *   AUDIO_ERR_PROVIDER_ERROR       = -32144
 *   AUDIO_ERR_VOICE_INVALID        = -32145
 *   AUDIO_ERR_MODEL_UNAVAILABLE    = -32146
 *   AUDIO_ERR_CONTENT_REJECTED     = -32147
 */

"use strict";

const crypto = require("node:crypto");

const { attachInvokeContext } = require("./context");

const METHOD_VIDEO_GENERATE = "video/generate";
const METHOD_VIDEO_GET_JOB = "video/get_job";
const METHOD_VIDEO_CANCEL_JOB = "video/cancel_job";
const METHOD_AUDIO_SPEAK = "audio/speak";

const VIDEO_ERR_NOT_GRANTED = -32120;
const VIDEO_ERR_QUOTA_EXCEEDED = -32121;
const VIDEO_ERR_INVALID_REQUEST = -32122;
const VIDEO_ERR_PROVIDER_ERROR = -32123;
const VIDEO_ERR_MODEL_UNAVAILABLE = -32124;
const VIDEO_ERR_JOB_NOT_FOUND = -32125;
const VIDEO_ERR_JOB_NOT_CANCELLABLE = -32126;
const VIDEO_ERR_CONCURRENCY_EXCEEDED = -32127;
const VIDEO_ERR_CONTENT_REJECTED = -32128;
const AUDIO_ERR_NOT_GRANTED = -32140;
const AUDIO_ERR_QUOTA_EXCEEDED = -32141;
const AUDIO_ERR_TEXT_TOO_LONG = -32142;
const AUDIO_ERR_TOO_LONG = -32143;
const AUDIO_ERR_PROVIDER_ERROR = -32144;
const AUDIO_ERR_VOICE_INVALID = -32145;
const AUDIO_ERR_MODEL_UNAVAILABLE = -32146;
const AUDIO_ERR_CONTENT_REJECTED = -32147;

const TERMINAL_JOB_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);

class MediaError extends Error {
  constructor(code, message, data) {
    super(`[${code}] ${message}`);
    this.name = "MediaError";
    this.code = code;
    this.data = data || {};
  }
}

/** generateAwait wall clock elapsed — job may still be rendering; carries `.jobId`. */
class VideoJobTimeout extends MediaError {
  constructor(jobId, waitedMs) {
    super(
      VIDEO_ERR_PROVIDER_ERROR,
      `video job ${jobId} still rendering after ${Math.round(waitedMs / 1000)}s`
    );
    this.name = "VideoJobTimeout";
    this.jobId = jobId;
  }
}

class MediaClient {
  constructor(opts = {}) {
    this._writeFrame =
      opts.writeFrame ||
      ((msg) => {
        process.stdout.write(JSON.stringify(msg) + "\n");
      });
    /** @type {Map<string, {resolve: Function, reject: Function, timer: NodeJS.Timeout}>} */
    this._pending = new Map();
    this._disabledReason = null;
  }

  disable(reason) {
    this._disabledReason = reason;
  }

  /**
   * Submit an async video generation job. With `imageUrl` the clip is
   * driven by that image (image-to-video; `aspectRatio` then follows it).
   * Resolves immediately to `{jobId, state:"queued", estimatedCostCU, deadlineAt}`.
   *
   * @param {{
   *   prompt: string,
   *   imageUrl?: string,
   *   durationSec?: number,
   *   resolution?: string,        // "480p"|"768p"|"1080p" (model-dependent)
   *   aspectRatio?: string,       // "16:9"|"9:16"|"1:1"…
   *   generateAudio?: boolean,    // audio-capable models only
   *   model?: string,             // catalog model hint
   *   clientTag?: string,
   *   timeoutMs?: number,         // default 120_000
   * }} opts
   */
  videoGenerate(opts) {
    const {
      prompt,
      imageUrl,
      durationSec,
      resolution,
      aspectRatio,
      generateAudio,
      model,
      clientTag,
      timeoutMs = 120_000,
    } = opts;
    const params = { prompt };
    if (imageUrl != null) params.imageUrl = imageUrl;
    if (durationSec != null) params.durationSec = Number(durationSec);
    if (resolution != null) params.resolution = resolution;
    if (aspectRatio != null) params.aspectRatio = aspectRatio;
    if (generateAudio != null) params.generateAudio = Boolean(generateAudio);
    if (model != null) params.model = model;
    if (clientTag != null) params.clientTag = clientTag;
    return this._call(METHOD_VIDEO_GENERATE, params, timeoutMs);
  }

  /**
   * Authoritative job snapshot. On `succeeded` the `result.url` presigned
   * GET is freshly re-signed (~30 min TTL) — call again after expiry.
   */
  videoGetJob(jobId, { timeoutMs = 60_000 } = {}) {
    return this._call(METHOD_VIDEO_GET_JOB, { jobId }, timeoutMs);
  }

  /**
   * Cancel a job. Queued = guaranteed cancel + full refund; running cancel
   * is best-effort (provider may refuse — the job then runs to terminal).
   */
  videoCancelJob(jobId, { timeoutMs = 60_000 } = {}) {
    return this._call(METHOD_VIDEO_CANCEL_JOB, { jobId }, timeoutMs);
  }

  /**
   * Submit + poll until terminal. Resolves with the terminal job view on
   * success; rejects with MediaError on failed/cancelled/expired and with
   * VideoJobTimeout (carrying `.jobId`) when `awaitTimeoutMs` elapses —
   * the job keeps rendering server-side (recover via videoGetJob).
   *
   * @param {object} opts — videoGenerate opts plus:
   *   `pollIntervalMs` (default 5000), `awaitTimeoutMs` (default 540000),
   *   `onProgress` (fn receiving the job `progress` object).
   */
  async generateAwait(opts) {
    const {
      pollIntervalMs = 5000,
      awaitTimeoutMs = 540_000,
      onProgress,
      ...genOpts
    } = opts;
    const created = await this.videoGenerate(genOpts);
    const jobId = created.jobId;
    let waited = 0;
    while (waited < awaitTimeoutMs) {
      await new Promise((r) => setTimeout(r, pollIntervalMs));
      waited += pollIntervalMs;
      const view = await this.videoGetJob(jobId);
      if (onProgress && view.progress) {
        try {
          onProgress(view.progress);
        } catch (_) {
          /* ignore */
        }
      }
      if (TERMINAL_JOB_STATES.has(view.state)) {
        if (view.state === "succeeded") return view;
        const err = view.error || {};
        throw new MediaError(
          VIDEO_ERR_PROVIDER_ERROR,
          `video job ${jobId} ended as ${view.state}: ${
            err.message || err.code || "unknown"
          }`,
          { jobId, state: view.state }
        );
      }
    }
    throw new VideoJobTimeout(jobId, waited);
  }

  /**
   * Synchronous TTS (`audio/speak`; EXECUTA_TTS billing). Resolves to
   * `{url, mimeType, charCount, billedCostCU, model, voice}` (or `audioBase64` with
   * `delivery:"inline"`). -32145 carries valid voices in `.data.allowed`.
   *
   * @param {{
   *   text: string,
   *   voice?: string,
   *   language?: string,
   *   timestamps?: boolean,       // eleven-v3 word timestamps only
   *   format?: string,            // "mp3"|"wav"
   *   speed?: number,             // 0.5–2.0
   *   delivery?: string,          // "url"(default)|"inline"
   *   model?: string,
   *   timeoutMs?: number,         // default 120_000
   * }} opts
   */
  speak(opts) {
    const {
      text,
      voice,
      language,
      timestamps,
      format,
      speed,
      delivery,
      model,
      timeoutMs = 120_000,
    } = opts;
    const params = { text };
    if (voice != null) params.voice = voice;
    if (language != null) params.language = language;
    if (timestamps != null) params.timestamps = Boolean(timestamps);
    if (format != null) params.format = format;
    if (speed != null) params.speed = Number(speed);
    if (delivery != null) params.delivery = delivery;
    if (model != null) params.model = model;
    return this._call(METHOD_AUDIO_SPEAK, params, timeoutMs);
  }

  dispatchResponse(msg) {
    if (!msg || typeof msg !== "object" || "method" in msg) return false;
    const id = msg.id;
    if (id == null) return false;
    const pending = this._pending.get(id);
    if (!pending) return false;
    this._pending.delete(id);
    clearTimeout(pending.timer);
    if (msg.error) {
      pending.reject(
        new MediaError(
          Number(msg.error.code) || -32603,
          String(msg.error.message || "unknown error"),
          msg.error.data
        )
      );
    } else {
      pending.resolve(msg.result || {});
    }
    return true;
  }

  _call(method, params, timeoutMs) {
    if (this._disabledReason) {
      const code =
        method === METHOD_AUDIO_SPEAK
          ? AUDIO_ERR_NOT_GRANTED
          : VIDEO_ERR_NOT_GRANTED;
      return Promise.reject(new MediaError(code, this._disabledReason));
    }
    const reqId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(reqId)) {
          reject(
            new MediaError(
              VIDEO_ERR_PROVIDER_ERROR,
              `${method} timed out after ${timeoutMs}ms`
            )
          );
        }
      }, timeoutMs);
      this._pending.set(reqId, { resolve, reject, timer });
      try {
        this._writeFrame({
          jsonrpc: "2.0",
          id: reqId,
          method,
          params: attachInvokeContext(params),
        });
      } catch (err) {
        clearTimeout(timer);
        this._pending.delete(reqId);
        reject(err);
      }
    });
  }
}

module.exports = {
  MediaClient,
  MediaError,
  VideoJobTimeout,
  METHOD_VIDEO_GENERATE,
  METHOD_VIDEO_GET_JOB,
  METHOD_VIDEO_CANCEL_JOB,
  METHOD_AUDIO_SPEAK,
  VIDEO_ERR_NOT_GRANTED,
  VIDEO_ERR_QUOTA_EXCEEDED,
  VIDEO_ERR_INVALID_REQUEST,
  VIDEO_ERR_PROVIDER_ERROR,
  VIDEO_ERR_MODEL_UNAVAILABLE,
  VIDEO_ERR_JOB_NOT_FOUND,
  VIDEO_ERR_JOB_NOT_CANCELLABLE,
  VIDEO_ERR_CONCURRENCY_EXCEEDED,
  VIDEO_ERR_CONTENT_REJECTED,
  AUDIO_ERR_NOT_GRANTED,
  AUDIO_ERR_QUOTA_EXCEEDED,
  AUDIO_ERR_TEXT_TOO_LONG,
  AUDIO_ERR_TOO_LONG,
  AUDIO_ERR_PROVIDER_ERROR,
  AUDIO_ERR_VOICE_INVALID,
  AUDIO_ERR_MODEL_UNAVAILABLE,
  AUDIO_ERR_CONTENT_REJECTED,
};
