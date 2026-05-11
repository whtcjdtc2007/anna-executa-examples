/**
 * StorageClient + FilesClient — issue reverse `storage/*` and `files/*`
 * JSON-RPC requests to the host Agent (Anna), which proxies to **Anna
 * Persistent Storage** (APS) — the platform-managed cross-Agent /
 * cross-App / cross-Tool KV + object store with a 5GB-per-user default
 * quota.
 *
 * Wire protocol (Plugin → Agent → Nexus REST):
 *   Plugin (us)                       Agent (host)              Nexus
 *   ──────────────────────────────────────────────────────────────────
 *   ← invoke(req_id=42, …)
 *   → storage/get(req_id=A, key=…)    → GET /api/v1/storage/kv?…
 *                                     ← 200 {value, etag, …}
 *   ← result | error
 *   → invoke result(req_id=42)
 *
 * Threading model identical to SamplingClient:
 *   - Construct one StorageClient + one FilesClient per process.
 *   - Feed every parsed JSON-RPC frame received on stdin to
 *     `client.dispatchResponse(msg)`. Frames that don't match a pending
 *     request return false; pass them on to your normal request
 *     handler.
 *
 * Error codes — keep in sync with matrix/src/executa/protocol.py:
 *   STORAGE_ERR_NOT_GRANTED         = -32021
 *   STORAGE_ERR_NOT_FOUND           = -32022
 *   STORAGE_ERR_PRECONDITION_FAILED = -32023
 *   STORAGE_ERR_QUOTA_EXCEEDED      = -32024
 *   STORAGE_ERR_VALUE_TOO_LARGE     = -32025
 *   STORAGE_ERR_RATE_LIMITED        = -32026
 *   STORAGE_ERR_INVALID_PATH        = -32027
 *   STORAGE_ERR_INVALID_REQUEST     = -32028
 *   STORAGE_ERR_UPSTREAM            = -32029
 *   STORAGE_ERR_TIMEOUT             = -32030 (SDK-local)
 */

"use strict";

const crypto = require("node:crypto");

// ─── Method names — keep in sync with matrix/src/executa/protocol.py ──

const METHOD_STORAGE_GET = "storage/get";
const METHOD_STORAGE_SET = "storage/set";
const METHOD_STORAGE_DELETE = "storage/delete";
const METHOD_STORAGE_LIST = "storage/list";

const METHOD_FILES_UPLOAD_BEGIN = "files/upload_begin";
const METHOD_FILES_UPLOAD_COMPLETE = "files/upload_complete";
const METHOD_FILES_DOWNLOAD_URL = "files/download_url";
const METHOD_FILES_LIST = "files/list";
const METHOD_FILES_DELETE = "files/delete";

const METHOD_USER_FILES_UPLOAD_BEGIN = "user_files/upload_begin";
const METHOD_USER_FILES_UPLOAD_COMPLETE = "user_files/upload_complete";
const METHOD_USER_FILES_DOWNLOAD_URL = "user_files/download_url";
const METHOD_USER_FILES_LIST = "user_files/list";
const METHOD_USER_FILES_DELETE = "user_files/delete";

const STORAGE_ERR_NOT_GRANTED = -32021;
const STORAGE_ERR_NOT_FOUND = -32022;
const STORAGE_ERR_PRECONDITION_FAILED = -32023;
const STORAGE_ERR_QUOTA_EXCEEDED = -32024;
const STORAGE_ERR_VALUE_TOO_LARGE = -32025;
const STORAGE_ERR_RATE_LIMITED = -32026;
const STORAGE_ERR_INVALID_PATH = -32027;
const STORAGE_ERR_INVALID_REQUEST = -32028;
const STORAGE_ERR_UPSTREAM = -32029;
const STORAGE_ERR_TIMEOUT = -32030;

class StorageError extends Error {
  constructor(code, message, data) {
    super(`[${code}] ${message}`);
    this.name = "StorageError";
    this.code = code;
    this.data = data || {};
  }
}

class _BaseRpcClient {
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

  _call(method, params, timeoutMs) {
    if (this._disabledReason) {
      return Promise.reject(
        new StorageError(STORAGE_ERR_NOT_GRANTED, this._disabledReason)
      );
    }
    const reqId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(reqId)) {
          reject(
            new StorageError(
              STORAGE_ERR_TIMEOUT,
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
        new StorageError(
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
}

class StorageClient extends _BaseRpcClient {
  /**
   * Read `key`. Resolves to `{ value, etag, exists }`. Missing keys return
   * `{ value: null, exists: false }` rather than rejecting.
   * @param {string} key
   * @param {{ scope?: "user"|"app"|"tool", timeoutMs?: number }} [opts]
   */
  get(key, opts = {}) {
    const { scope = "app", timeoutMs = 30_000 } = opts;
    return this._call(METHOD_STORAGE_GET, { key, scope }, timeoutMs);
  }

  /**
   * Write `key=value`. Resolves to `{ etag, generation, size_bytes }`.
   * Pass `if_match: <etag>` for optimistic concurrency.
   * @param {string} key
   * @param {*} value
   * @param {{ scope?: string, if_match?: string, ttl_seconds?: number, timeoutMs?: number }} [opts]
   */
  set(key, value, opts = {}) {
    const { scope = "app", if_match, ttl_seconds, timeoutMs = 30_000 } = opts;
    const params = { key, value, scope };
    if (if_match != null) params.if_match = if_match;
    if (ttl_seconds != null) params.ttl_seconds = ttl_seconds;
    return this._call(METHOD_STORAGE_SET, params, timeoutMs);
  }

  delete(key, opts = {}) {
    const { scope = "app", if_match, timeoutMs = 30_000 } = opts;
    const params = { key, scope };
    if (if_match != null) params.if_match = if_match;
    return this._call(METHOD_STORAGE_DELETE, params, timeoutMs);
  }

  /**
   * Paginate. Returns `{ items: [...], next_cursor }`.
   * @param {{ prefix?: string, cursor?: string, limit?: number, kind?: "kv"|"file", scope?: string, timeoutMs?: number }} [opts]
   */
  list(opts = {}) {
    const { prefix, cursor, limit, kind, scope = "app", timeoutMs = 30_000 } = opts;
    const params = { scope };
    if (prefix != null) params.prefix = prefix;
    if (cursor != null) params.cursor = cursor;
    if (limit != null) params.limit = limit;
    if (kind != null) params.kind = kind;
    return this._call(METHOD_STORAGE_LIST, params, timeoutMs);
  }
}

class FilesClient extends _BaseRpcClient {
  static _route(base, scope) {
    if (scope === "user") return base.replace("files/", "user_files/");
    return base;
  }

  /**
   * Returns `{ upload_id, put_url, headers, expires_at }`.
   * @param {{ path: string, size_bytes?: number, content_type?: string, metadata?: object, scope?: string, timeoutMs?: number }} opts
   */
  uploadBegin(opts) {
    const { path, size_bytes, content_type, metadata, scope = "app", timeoutMs = 60_000 } = opts;
    const params = { path, scope };
    if (size_bytes != null) params.size_bytes = size_bytes;
    if (content_type != null) params.content_type = content_type;
    if (metadata != null) params.metadata = metadata;
    return this._call(FilesClient._route(METHOD_FILES_UPLOAD_BEGIN, scope), params, timeoutMs);
  }

  /**
   * Confirm upload to host (host verifies the object landed in storage).
   * @param {{ path: string, etag?: string, size_bytes?: number, content_type?: string, scope?: string, timeoutMs?: number }} opts
   */
  uploadComplete(opts) {
    const { path, etag, size_bytes, content_type, scope = "app", timeoutMs = 60_000 } = opts;
    const params = { path, scope };
    if (etag != null) params.etag = etag;
    if (size_bytes != null) params.size_bytes = size_bytes;
    if (content_type != null) params.content_type = content_type;
    return this._call(FilesClient._route(METHOD_FILES_UPLOAD_COMPLETE, scope), params, timeoutMs);
  }

  /**
   * Returns `{ url, expires_at }`.
   * @param {{ path: string, expires_in?: number, scope?: string, timeoutMs?: number }} opts
   */
  downloadUrl(opts) {
    const { path, expires_in, scope = "app", timeoutMs = 30_000 } = opts;
    const params = { path, scope };
    if (expires_in != null) params.expires_in = expires_in;
    return this._call(FilesClient._route(METHOD_FILES_DOWNLOAD_URL, scope), params, timeoutMs);
  }

  list(opts = {}) {
    const { prefix, cursor, limit, scope = "app", timeoutMs = 30_000 } = opts;
    const params = { scope };
    if (prefix != null) params.prefix = prefix;
    if (cursor != null) params.cursor = cursor;
    if (limit != null) params.limit = limit;
    return this._call(FilesClient._route(METHOD_FILES_LIST, scope), params, timeoutMs);
  }

  delete(opts) {
    const { path, scope = "app", timeoutMs = 30_000 } = opts;
    return this._call(FilesClient._route(METHOD_FILES_DELETE, scope), { path, scope }, timeoutMs);
  }
}

/**
 * Build a single dispatch fn that routes an inbound message to whichever
 * client has a matching pending request. Use in your stdin reader loop.
 *
 * @param  {..._BaseRpcClient} clients
 * @returns {(msg: object) => boolean}
 */
function makeResponseRouter(...clients) {
  return (msg) => {
    for (const c of clients) {
      if (c.dispatchResponse(msg)) return true;
    }
    return false;
  };
}

module.exports = {
  StorageClient,
  FilesClient,
  StorageError,
  makeResponseRouter,
  METHOD_STORAGE_GET,
  METHOD_STORAGE_SET,
  METHOD_STORAGE_DELETE,
  METHOD_STORAGE_LIST,
  METHOD_FILES_UPLOAD_BEGIN,
  METHOD_FILES_UPLOAD_COMPLETE,
  METHOD_FILES_DOWNLOAD_URL,
  METHOD_FILES_LIST,
  METHOD_FILES_DELETE,
  METHOD_USER_FILES_UPLOAD_BEGIN,
  METHOD_USER_FILES_UPLOAD_COMPLETE,
  METHOD_USER_FILES_DOWNLOAD_URL,
  METHOD_USER_FILES_LIST,
  METHOD_USER_FILES_DELETE,
  STORAGE_ERR_NOT_GRANTED,
  STORAGE_ERR_NOT_FOUND,
  STORAGE_ERR_PRECONDITION_FAILED,
  STORAGE_ERR_QUOTA_EXCEEDED,
  STORAGE_ERR_VALUE_TOO_LARGE,
  STORAGE_ERR_RATE_LIMITED,
  STORAGE_ERR_INVALID_PATH,
  STORAGE_ERR_INVALID_REQUEST,
  STORAGE_ERR_UPSTREAM,
  STORAGE_ERR_TIMEOUT,
};
