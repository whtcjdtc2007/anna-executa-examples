/**
 * HostUploadClient — issue reverse `host/uploadFile` JSON-RPC requests
 * to upload transient bytes to Anna's R2 bucket. Returns a transient
 * HTTPS URL the plugin can immediately feed into `image/edit`,
 * `sampling/createMessage`, or any host capability expecting an
 * HTTPS-reachable asset.
 *
 * The host gates uploads against the user's `upload_grant`
 * (MIME allowlist, per-file size cap, total bytes quota).
 *
 * Three modes (selected via `mode=` parameter):
 *   - "inline":    base64 payload, ≤8MB. Simplest; one round-trip.
 *   - "negotiate": host returns a presigned PUT URL; plugin uploads
 *                  bytes directly to R2. Best for >8MB.
 *   - "confirm":   after PUT to a presigned URL, confirms the upload
 *                  completed and returns the transient download URL.
 *
 * Error codes — keep in sync with matrix/src/executa/protocol.py:
 *   UPLOAD_ERR_NOT_GRANTED        = -32201
 *   UPLOAD_ERR_QUOTA_EXCEEDED     = -32202
 *   UPLOAD_ERR_INVALID_REQUEST    = -32203
 *   UPLOAD_ERR_TOO_LARGE          = -32204
 *   UPLOAD_ERR_MIME_REJECTED      = -32205
 *   UPLOAD_ERR_PURPOSE_REJECTED   = -32206
 *   UPLOAD_ERR_STORAGE_ERROR      = -32207
 *   UPLOAD_ERR_TIMEOUT            = -32208
 *   UPLOAD_ERR_USER_DENIED        = -32209
 *   UPLOAD_ERR_NOT_NEGOTIATED     = -32210
 *   UPLOAD_ERR_MAX_FILES_EXCEEDED = -32211
 *   UPLOAD_ERR_NOT_FOUND          = -32212
 *   UPLOAD_ERR_PRESIGN_FAILED     = -32213
 */

"use strict";

const crypto = require("node:crypto");

const METHOD_HOST_UPLOAD_FILE = "host/uploadFile";

const UPLOAD_ERR_NOT_GRANTED = -32201;
const UPLOAD_ERR_QUOTA_EXCEEDED = -32202;
const UPLOAD_ERR_INVALID_REQUEST = -32203;
const UPLOAD_ERR_TOO_LARGE = -32204;
const UPLOAD_ERR_MIME_REJECTED = -32205;
const UPLOAD_ERR_PURPOSE_REJECTED = -32206;
const UPLOAD_ERR_STORAGE_ERROR = -32207;
const UPLOAD_ERR_TIMEOUT = -32208;
const UPLOAD_ERR_USER_DENIED = -32209;
const UPLOAD_ERR_NOT_NEGOTIATED = -32210;
const UPLOAD_ERR_MAX_FILES_EXCEEDED = -32211;
const UPLOAD_ERR_NOT_FOUND = -32212;
const UPLOAD_ERR_PRESIGN_FAILED = -32213;

const MAX_INLINE_BYTES = 8 * 1024 * 1024;

class UploadError extends Error {
  constructor(code, message, data) {
    super(`[${code}] ${message}`);
    this.name = "UploadError";
    this.code = code;
    this.data = data || {};
  }
}

class HostUploadClient {
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
   * Upload raw `content` (Buffer or Uint8Array) inline via base64.
   * Resolves to `{ download_url, r2_key, expires_at, size_bytes }`.
   *
   * @param {{
   *   filename: string,
   *   mimeType: string,
   *   content: Buffer | Uint8Array,
   *   purpose?: string,
   *   metadata?: object,
   *   timeoutMs?: number,
   * }} opts
   */
  uploadInline(opts) {
    const {
      filename,
      mimeType,
      content,
      purpose,
      metadata,
      timeoutMs = 120_000,
    } = opts;
    const buf = Buffer.isBuffer(content) ? content : Buffer.from(content);
    if (buf.length > MAX_INLINE_BYTES) {
      return Promise.reject(
        new UploadError(
          UPLOAD_ERR_TOO_LARGE,
          `inline payload ${buf.length} bytes exceeds SDK cap ${MAX_INLINE_BYTES} — use negotiate() instead`
        )
      );
    }
    const params = {
      mode: "inline",
      filename,
      mime_type: mimeType,
      content_b64: buf.toString("base64"),
    };
    if (purpose != null) params.purpose = purpose;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_HOST_UPLOAD_FILE, params, timeoutMs);
  }

  /**
   * Negotiate a presigned PUT URL for direct upload to R2.
   * Resolves to `{ put_url, headers, r2_key, expires_at }`.
   *
   * @param {{
   *   filename: string,
   *   mimeType: string,
   *   sizeBytes: number,
   *   purpose?: string,
   *   metadata?: object,
   *   timeoutMs?: number,
   * }} opts
   */
  negotiate(opts) {
    const {
      filename,
      mimeType,
      sizeBytes,
      purpose,
      metadata,
      timeoutMs = 60_000,
    } = opts;
    const params = {
      mode: "negotiate",
      filename,
      mime_type: mimeType,
      size_bytes: Number(sizeBytes),
    };
    if (purpose != null) params.purpose = purpose;
    if (metadata != null) params.metadata = metadata;
    return this._call(METHOD_HOST_UPLOAD_FILE, params, timeoutMs);
  }

  /**
   * Confirm a presigned upload completed; returns transient download URL.
   * Resolves to `{ download_url, r2_key, size_bytes, expires_at }`.
   *
   * @param {{ r2Key: string, timeoutMs?: number }} opts
   */
  confirm(opts) {
    const { r2Key, timeoutMs = 30_000 } = opts;
    return this._call(
      METHOD_HOST_UPLOAD_FILE,
      { mode: "confirm", r2_key: r2Key },
      timeoutMs
    );
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
        new UploadError(
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
        new UploadError(UPLOAD_ERR_NOT_GRANTED, this._disabledReason)
      );
    }
    const reqId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(reqId)) {
          reject(
            new UploadError(
              UPLOAD_ERR_TIMEOUT,
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
  HostUploadClient,
  UploadError,
  METHOD_HOST_UPLOAD_FILE,
  UPLOAD_ERR_NOT_GRANTED,
  UPLOAD_ERR_QUOTA_EXCEEDED,
  UPLOAD_ERR_INVALID_REQUEST,
  UPLOAD_ERR_TOO_LARGE,
  UPLOAD_ERR_MIME_REJECTED,
  UPLOAD_ERR_PURPOSE_REJECTED,
  UPLOAD_ERR_STORAGE_ERROR,
  UPLOAD_ERR_TIMEOUT,
  UPLOAD_ERR_USER_DENIED,
  UPLOAD_ERR_NOT_NEGOTIATED,
  UPLOAD_ERR_MAX_FILES_EXCEEDED,
  UPLOAD_ERR_NOT_FOUND,
  UPLOAD_ERR_PRESIGN_FAILED,
};
