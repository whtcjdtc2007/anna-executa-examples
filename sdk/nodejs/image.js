/**
 * ImageClient — issue reverse `image/generate` / `image/edit` (sync) and the
 * async-job family `image/{generate_async, edit_async, get_job, cancel_job,
 * resign}` JSON-RPC requests to the host Agent (Anna), which proxies to Nexus's
 * `/api/v1/copilot/image/*` endpoints using a short-lived `image_token`
 * (aud=executa-image) the host minted at invoke time.
 *
 * Async jobs (`imageGenerateAsync` / `imageEditAsync` → `ijob_…`) mirror the
 * music job surface: submit returns at once, poll `imageGetJob` (or use
 * `imageGenerateAwait` / `imageEditAwait`), `result.images[].url` is re-signed
 * on every get_job, and `imageResign` re-signs any already-paid artifact by
 * `r2Key` for up to 7 days. Prefer jobs for 2K/4K or quality=high renders.
 *
 * The plugin never sees the LLM API key — it just pays in image counts,
 * gated by the user's grant (`image_grant` block on UserExecuta.custom_config).
 *
 * Wire protocol (Plugin → Agent → Nexus):
 *   Plugin (us)                       Agent (host)                Nexus
 *   ─────────────────────────────────────────────────────────────────────
 *   ← invoke(req_id=42, …)
 *   → image/generate(req_id=A, …)    → POST /copilot/image/generate
 *                                       header: Bearer <image_token>
 *                                       body: {prompt, n, size, …}
 *                                     ← 200 {images:[…], model, quota_used}
 *   ← result | error
 *
 * Threading model identical to SamplingClient / StorageClient:
 *   - Construct one ImageClient per process.
 *   - Feed every parsed JSON-RPC frame received on stdin to
 *     `image.dispatchResponse(msg)`; pair with `makeResponseRouter()`.
 *
 * Error codes — keep in sync with matrix/src/executa/protocol.py:
 *   IMAGE_ERR_NOT_GRANTED            = -32101
 *   IMAGE_ERR_QUOTA_EXCEEDED         = -32102
 *   IMAGE_ERR_PROVIDER_ERROR         = -32103
 *   IMAGE_ERR_INVALID_REQUEST        = -32104
 *   IMAGE_ERR_TIMEOUT                = -32105
 *   IMAGE_ERR_MAX_IMAGES_EXCEEDED    = -32106
 *   IMAGE_ERR_NOT_NEGOTIATED         = -32107
 *   IMAGE_ERR_USER_DENIED            = -32108
 *   IMAGE_ERR_NO_MODEL_AVAILABLE     = -32109
 *   IMAGE_ERR_STORAGE_ERROR          = -32110
 *   IMAGE_ERR_JOB_NOT_FOUND          = -32113
 *   IMAGE_ERR_JOB_NOT_CANCELLABLE    = -32114
 *   IMAGE_ERR_CONTENT_REJECTED       = -32115
 *   IMAGE_ERR_JOB_EXPIRED            = -32116
 *   IMAGE_ERR_ASSET_NOT_FOUND        = -32117
 *   IMAGE_ERR_EDIT_NOT_SUPPORTED     = -32311
 *   IMAGE_ERR_MASK_UNSUPPORTED       = -32312
 *   IMAGE_ERR_N_UNSUPPORTED          = -32313
 *   IMAGE_ERR_REFERENCE_FETCH_FAILED = -32314
 */

"use strict";

const crypto = require("node:crypto");

const { attachInvokeContext } = require("./context");

const METHOD_IMAGE_GENERATE = "image/generate";
const METHOD_IMAGE_EDIT = "image/edit";
// Async jobs + re-sign (platform-media-generation-image-jobs.md §11)
const METHOD_IMAGE_GENERATE_ASYNC = "image/generate_async";
const METHOD_IMAGE_EDIT_ASYNC = "image/edit_async";
const METHOD_IMAGE_GET_JOB = "image/get_job";
const METHOD_IMAGE_CANCEL_JOB = "image/cancel_job";
const METHOD_IMAGE_RESIGN = "image/resign";

const TERMINAL_JOB_STATES = new Set(["succeeded", "failed", "cancelled", "expired"]);

const IMAGE_ERR_NOT_GRANTED = -32101;
const IMAGE_ERR_QUOTA_EXCEEDED = -32102;
const IMAGE_ERR_PROVIDER_ERROR = -32103;
const IMAGE_ERR_INVALID_REQUEST = -32104;
const IMAGE_ERR_TIMEOUT = -32105;
const IMAGE_ERR_MAX_IMAGES_EXCEEDED = -32106;
const IMAGE_ERR_NOT_NEGOTIATED = -32107;
const IMAGE_ERR_USER_DENIED = -32108;
const IMAGE_ERR_NO_MODEL_AVAILABLE = -32109;
const IMAGE_ERR_STORAGE_ERROR = -32110;
const IMAGE_ERR_JOB_NOT_FOUND = -32113;
const IMAGE_ERR_JOB_NOT_CANCELLABLE = -32114;
const IMAGE_ERR_CONTENT_REJECTED = -32115;
const IMAGE_ERR_JOB_EXPIRED = -32116;
const IMAGE_ERR_ASSET_NOT_FOUND = -32117;
const IMAGE_ERR_EDIT_NOT_SUPPORTED = -32311;
const IMAGE_ERR_MASK_UNSUPPORTED = -32312;
const IMAGE_ERR_N_UNSUPPORTED = -32313;
const IMAGE_ERR_REFERENCE_FETCH_FAILED = -32314;

class ImageError extends Error {
  constructor(code, message, data) {
    super(`[${code}] ${message}`);
    this.name = "ImageError";
    this.code = code;
    this.data = data || {};
  }
}

/** imageGenerateAwait / imageEditAwait wall clock elapsed — job may still be
 *  rendering; carries `.jobId` so the plugin can resume with `imageGetJob`. */
class ImageJobTimeout extends ImageError {
  constructor(jobId, waitedMs) {
    super(
      IMAGE_ERR_PROVIDER_ERROR,
      `image job ${jobId} still rendering after ${Math.round(waitedMs / 1000)}s`,
      { jobId }
    );
    this.name = "ImageJobTimeout";
    this.jobId = jobId;
  }
}

function jobParams({
  prompt,
  n = 1,
  size,
  aspectRatio,
  model,
  params,
  fit = "nearest",
  clientTag,
  idempotencyKey,
}) {
  const out = { prompt, n: Number(n) };
  if (size != null) out.size = size;
  if (aspectRatio != null) out.aspect_ratio = aspectRatio;
  if (model != null) out.model = model;
  if (params && Object.keys(params).length) out.params = { ...params };
  if (fit != null) out.fit = fit;
  if (clientTag != null) out.clientTag = clientTag;
  if (idempotencyKey != null) out.idempotencyKey = idempotencyKey;
  return out;
}

class ImageClient {
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
   * Generate `n` images from a text `prompt`.
   * Resolves to `{ images: [{url, mimeType, ...}], model, quota_used }`.
   *
   * Advanced options (host validates; unsupported models silently ignore
   * them — no extra charge in that case):
   * - `quality`: "low"|"medium"|"high" (GPT-Image family; affects cost)
   * - `resolution`: "0.5K"|"1K"|"2K"|"4K" (Nano-Banana family; 0.75x/1x/1.5x/2x rate)
   * - `output_format`: "png"|"jpeg"|"webp"
   * - `enable_web_search`: boolean (Nano Banana 2; small surcharge)
   * - `thinking_level`: "minimal"|"high" (Nano Banana 2; "high" adds surcharge)
   *
   * @param {{
   *   prompt: string,
   *   n?: number,                        // default 1
   *   size?: string,                     // e.g. "1024x1024"
   *   reference_image_urls?: string[],   // for img2img-style hint
   *   quality?: string,
   *   resolution?: string,
   *   output_format?: string,
   *   enable_web_search?: boolean,
   *   thinking_level?: string,
   *   modelPreferences?: object,
   *   metadata?: object,
   *   timeoutMs?: number,                // default 120_000
   * }} opts
   */
  generate(opts) {
    const {
      prompt,
      n = 1,
      size,
      reference_image_urls,
      quality,
      resolution,
      output_format,
      enable_web_search,
      thinking_level,
      modelPreferences,
      metadata,
      timeoutMs = 120_000,
    } = opts;
    const params = { prompt, n: Number(n) };
    if (size != null) params.size = size;
    if (reference_image_urls != null)
      params.reference_image_urls = reference_image_urls;
    if (quality != null) params.quality = quality;
    if (resolution != null) params.resolution = resolution;
    if (output_format != null) params.output_format = output_format;
    if (enable_web_search != null)
      params.enable_web_search = Boolean(enable_web_search);
    if (thinking_level != null) params.thinking_level = thinking_level;
    if (modelPreferences != null) params.modelPreferences = modelPreferences;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_IMAGE_GENERATE, params, timeoutMs);
  }

  /**
   * Edit a source image. `mask_url` is optional; without it the provider
   * does a whole-image edit, with it only masked pixels change (requires
   * a mask-capable model such as GPT Image 2 — otherwise -32312).
   *
   * Resolves to `{ images: […], model, quota_used }`.
   * Codes -32311/-32312 indicate provider does not support edit / masking.
   *
   * @param {{
   *   image_url: string,
   *   prompt: string,
   *   mask_url?: string,
   *   n?: number,
   *   size?: string,
   *   quality?: string,
   *   resolution?: string,
   *   output_format?: string,
   *   modelPreferences?: object,
   *   metadata?: object,
   *   timeoutMs?: number,
   * }} opts
   */
  edit(opts) {
    const {
      image_url,
      prompt,
      mask_url,
      n = 1,
      size,
      quality,
      resolution,
      output_format,
      modelPreferences,
      metadata,
      timeoutMs = 120_000,
    } = opts;
    const params = { image_url, prompt, n: Number(n) };
    if (mask_url != null) params.mask_url = mask_url;
    if (size != null) params.size = size;
    if (quality != null) params.quality = quality;
    if (resolution != null) params.resolution = resolution;
    if (output_format != null) params.output_format = output_format;
    if (modelPreferences != null) params.modelPreferences = modelPreferences;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_IMAGE_EDIT, params, timeoutMs);
  }

  // ---- async jobs + re-sign (image-jobs §11.5) ----

  /**
   * Submit an async text-to-image / reference-image job. Resolves with
   * `{jobId:"ijob_…", kind:"image", state:"queued", estimatedCostCU, deadlineAt, idempotentReplay}`.
   * One job = n images (billed up-front, settled on actual output). Pass
   * `idempotencyKey` (e.g. `${invokeId}:${attempt}`) so retries never double-charge.
   */
  imageGenerateAsync(opts) {
    const { referenceImageUrls, timeoutMs = 60_000, ...rest } = opts;
    const params = jobParams(rest);
    if (referenceImageUrls && referenceImageUrls.length) {
      params.reference_image_urls = [...referenceImageUrls];
    }
    return this._call(METHOD_IMAGE_GENERATE_ASYNC, params, timeoutMs);
  }

  /** Submit an async edit job (optionally mask-based; -32312 when the model cannot mask). */
  imageEditAsync(opts) {
    const { imageUrl, maskUrl, timeoutMs = 60_000, ...rest } = opts;
    const params = jobParams(rest);
    params.image_url = imageUrl;
    if (maskUrl != null) params.mask_url = maskUrl;
    return this._call(METHOD_IMAGE_EDIT_ASYNC, params, timeoutMs);
  }

  /** Authoritative job view; `result.images[].url` is re-signed on every call (~30 min). */
  imageGetJob(jobId, { timeoutMs = 60_000 } = {}) {
    return this._call(METHOD_IMAGE_GET_JOB, { jobId }, timeoutMs);
  }

  /** queued = guaranteed + full refund; running = best-effort (provider may refuse). */
  imageCancelJob(jobId, { timeoutMs = 60_000 } = {}) {
    return this._call(METHOD_IMAGE_CANCEL_JOB, { jobId }, timeoutMs);
  }

  /** Re-sign already-paid artifacts by r2Key (1–20). Resolves `{urls:[{r2Key,url,expiresIn}]}`
   *  in input order; a key that is not yours fails the whole call with -32117. */
  imageResign(r2Keys, { timeoutMs = 60_000 } = {}) {
    return this._call(METHOD_IMAGE_RESIGN, { r2Keys: [...r2Keys] }, timeoutMs);
  }

  async _awaitJob(created, { pollIntervalMs, awaitTimeoutMs, onProgress }) {
    const jobId = created.jobId;
    const terminalError = (view) => {
      const err = view.error || {};
      const code =
        err.code === "IMAGE_CONTENT_REJECTED"
          ? IMAGE_ERR_CONTENT_REJECTED
          : err.code === "IMAGE_JOB_EXPIRED"
            ? IMAGE_ERR_JOB_EXPIRED
            : IMAGE_ERR_PROVIDER_ERROR;
      return new ImageError(
        code,
        `image job ${jobId} ended as ${view.state}: ${err.message || err.code || "unknown"}`,
        { jobId, state: view.state, error: err }
      );
    };
    if (TERMINAL_JOB_STATES.has(created.state)) {
      if (created.state === "succeeded") return created;
      throw terminalError(created);
    }
    let waited = 0;
    while (waited < awaitTimeoutMs) {
      await new Promise((r) => setTimeout(r, pollIntervalMs));
      waited += pollIntervalMs;
      const view = await this.imageGetJob(jobId);
      if (onProgress && view.progress) {
        try {
          onProgress(view.progress);
        } catch (_) {
          /* ignore */
        }
      }
      if (TERMINAL_JOB_STATES.has(view.state)) {
        if (view.state === "succeeded") return view;
        throw terminalError(view);
      }
    }
    throw new ImageJobTimeout(jobId, waited);
  }

  /**
   * imageGenerateAsync + poll until terminal (pure polling; same cadence as
   * musicGenerateAwait: 5 s / 9 min). Resolves the terminal job view; rejects
   * with ImageError on failed / cancelled / expired and ImageJobTimeout
   * (carrying `.jobId`) when the wall clock elapses first. Keep awaitTimeoutMs
   * within the invoke budget — for longer renders return `jobId` as the invoke
   * result and resume with imageGetJob.
   */
  async imageGenerateAwait(opts) {
    const { pollIntervalMs = 5000, awaitTimeoutMs = 540_000, onProgress, ...genOpts } = opts;
    const created = await this.imageGenerateAsync(genOpts);
    return this._awaitJob(created, { pollIntervalMs, awaitTimeoutMs, onProgress });
  }

  /** imageEditAsync + poll until terminal (see imageGenerateAwait). */
  async imageEditAwait(opts) {
    const { pollIntervalMs = 5000, awaitTimeoutMs = 540_000, onProgress, ...editOpts } = opts;
    const created = await this.imageEditAsync(editOpts);
    return this._awaitJob(created, { pollIntervalMs, awaitTimeoutMs, onProgress });
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
        new ImageError(
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
      return Promise.reject(
        new ImageError(IMAGE_ERR_NOT_GRANTED, this._disabledReason)
      );
    }
    const reqId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(reqId)) {
          reject(
            new ImageError(
              IMAGE_ERR_TIMEOUT,
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
  ImageClient,
  ImageError,
  ImageJobTimeout,
  METHOD_IMAGE_GENERATE,
  METHOD_IMAGE_EDIT,
  METHOD_IMAGE_GENERATE_ASYNC,
  METHOD_IMAGE_EDIT_ASYNC,
  METHOD_IMAGE_GET_JOB,
  METHOD_IMAGE_CANCEL_JOB,
  METHOD_IMAGE_RESIGN,
  IMAGE_ERR_NOT_GRANTED,
  IMAGE_ERR_QUOTA_EXCEEDED,
  IMAGE_ERR_PROVIDER_ERROR,
  IMAGE_ERR_INVALID_REQUEST,
  IMAGE_ERR_TIMEOUT,
  IMAGE_ERR_MAX_IMAGES_EXCEEDED,
  IMAGE_ERR_NOT_NEGOTIATED,
  IMAGE_ERR_USER_DENIED,
  IMAGE_ERR_NO_MODEL_AVAILABLE,
  IMAGE_ERR_STORAGE_ERROR,
  IMAGE_ERR_JOB_NOT_FOUND,
  IMAGE_ERR_JOB_NOT_CANCELLABLE,
  IMAGE_ERR_CONTENT_REJECTED,
  IMAGE_ERR_JOB_EXPIRED,
  IMAGE_ERR_ASSET_NOT_FOUND,
  IMAGE_ERR_EDIT_NOT_SUPPORTED,
  IMAGE_ERR_MASK_UNSUPPORTED,
  IMAGE_ERR_N_UNSUPPORTED,
  IMAGE_ERR_REFERENCE_FETCH_FAILED,
};
