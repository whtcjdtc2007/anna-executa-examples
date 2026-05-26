/**
 * ImageClient — issue reverse `image/generate` and `image/edit` JSON-RPC
 * requests to the host Agent (Anna), which proxies to Nexus's
 * `/api/v1/copilot/image/*` endpoints using a short-lived `image_token`
 * (aud=executa-image) the host minted at invoke time.
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
 *   IMAGE_ERR_EDIT_NOT_SUPPORTED     = -32311
 *   IMAGE_ERR_MASK_UNSUPPORTED       = -32312
 *   IMAGE_ERR_N_UNSUPPORTED          = -32313
 *   IMAGE_ERR_REFERENCE_FETCH_FAILED = -32314
 */

"use strict";

const crypto = require("node:crypto");

const METHOD_IMAGE_GENERATE = "image/generate";
const METHOD_IMAGE_EDIT = "image/edit";

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
   * @param {{
   *   prompt: string,
   *   n?: number,                        // default 1
   *   size?: string,                     // e.g. "1024x1024"
   *   reference_image_urls?: string[],   // for img2img-style hint
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
      modelPreferences,
      metadata,
      timeoutMs = 120_000,
    } = opts;
    const params = { prompt, n: Number(n) };
    if (size != null) params.size = size;
    if (reference_image_urls != null)
      params.reference_image_urls = reference_image_urls;
    if (modelPreferences != null) params.modelPreferences = modelPreferences;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_IMAGE_GENERATE, params, timeoutMs);
  }

  /**
   * Edit a source image. `mask_url` is optional; without it the provider
   * does a whole-image edit, with it only masked pixels change.
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
      modelPreferences,
      metadata,
      timeoutMs = 120_000,
    } = opts;
    const params = { image_url, prompt, n: Number(n) };
    if (mask_url != null) params.mask_url = mask_url;
    if (size != null) params.size = size;
    if (modelPreferences != null) params.modelPreferences = modelPreferences;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_IMAGE_EDIT, params, timeoutMs);
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
          params,
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
  METHOD_IMAGE_GENERATE,
  METHOD_IMAGE_EDIT,
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
  IMAGE_ERR_EDIT_NOT_SUPPORTED,
  IMAGE_ERR_MASK_UNSUPPORTED,
  IMAGE_ERR_N_UNSUPPORTED,
  IMAGE_ERR_REFERENCE_FETCH_FAILED,
};
