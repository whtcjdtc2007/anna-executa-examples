/**
 * SamplingClient — issue reverse `sampling/createMessage` JSON-RPC requests
 * to the host Agent (Anna).
 *
 * Why reverse RPC?
 *   - Plugins do NOT need their own LLM API key — billing/quotas/model
 *     routing are owned by the host (Anna).
 *   - Plugins describe a desired model via `modelPreferences` (MCP
 *     convention) and let the host pick a concrete model based on the
 *     user's saved preferences.
 *
 * Wire protocol (Executa v2):
 *   Plugin (us)                                Agent (host)
 *   ───────────────────────────────────────────────────────────────
 *   ← invoke(req_id=42, …)
 *   → sampling/createMessage(req_id=A)
 *   ← result | error                            (host replies)
 *   → invoke result(req_id=42)
 *
 * Threading model:
 *   - Construct one instance per process; share across tools.
 *   - Wire it up by feeding EVERY parsed JSON-RPC frame received on
 *     stdin to `client.dispatchResponse(msg)`. Frames that don't match
 *     a pending request are returned to your normal request handler.
 */

"use strict";

const crypto = require("node:crypto");

// ─── Constants — keep in sync with matrix/src/executa/protocol.py ─────

const PROTOCOL_VERSION_V1 = "1.1";
const PROTOCOL_VERSION_V2 = "2.0";

const METHOD_INITIALIZE = "initialize";
const METHOD_SHUTDOWN = "shutdown";
const METHOD_SAMPLING_CREATE_MESSAGE = "sampling/createMessage";

const SAMPLING_ERR_NOT_GRANTED = -32001;
const SAMPLING_ERR_QUOTA_EXCEEDED = -32002;
const SAMPLING_ERR_PROVIDER_ERROR = -32003;
const SAMPLING_ERR_INVALID_REQUEST = -32004;
const SAMPLING_ERR_TIMEOUT = -32005;
const SAMPLING_ERR_MAX_CALLS_EXCEEDED = -32006;
const SAMPLING_ERR_MAX_TOKENS_EXCEEDED = -32007;
const SAMPLING_ERR_NOT_NEGOTIATED = -32008;
const SAMPLING_ERR_USER_DENIED = -32009;

class SamplingError extends Error {
  constructor(code, message, data) {
    super(`[${code}] ${message}`);
    this.name = "SamplingError";
    this.code = code;
    this.data = data || {};
  }
}

class SamplingClient {
  /**
   * @param {object} [opts]
   * @param {(msg: object) => void} [opts.writeFrame] — defaults to writing
   *   a single newline-delimited JSON object to `process.stdout`.
   */
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

  /**
   * Mark sampling as unavailable (e.g. host did not negotiate v2).
   * @param {string} reason
   */
  disable(reason) {
    this._disabledReason = reason;
  }

  /**
   * Ask the host to run an LLM completion. Returns the host result object.
   *
   * @param {object} params
   * @param {Array<{role:"user"|"assistant"|"system", content:{type:"text", text:string}}>} params.messages
   * @param {number} params.maxTokens               — required, host enforces hard cap.
   * @param {string} [params.systemPrompt]
   * @param {number} [params.temperature]
   * @param {string[]} [params.stopSequences]
   * @param {object} [params.modelPreferences]      — MCP shape, omit to fall
   *                                                  back to the user's
   *                                                  preferred model.
   * @param {"none"} [params.includeContext]        — Phase 1 supports only "none".
   * @param {Object<string,string>} [params.metadata]
   * @param {number} [params.timeoutMs]             — default 90 000 ms.
   * @returns {Promise<object>}
   */
  createMessage(params) {
    if (this._disabledReason) {
      return Promise.reject(
        new SamplingError(SAMPLING_ERR_NOT_NEGOTIATED, this._disabledReason)
      );
    }
    const {
      messages,
      maxTokens,
      systemPrompt,
      temperature,
      stopSequences,
      modelPreferences,
      includeContext = "none",
      metadata,
      timeoutMs = 90_000,
    } = params || {};

    if (!Array.isArray(messages) || messages.length === 0) {
      return Promise.reject(new TypeError("messages must be a non-empty array"));
    }
    if (!Number.isInteger(maxTokens) || maxTokens <= 0) {
      return Promise.reject(new TypeError("maxTokens must be a positive integer"));
    }

    const reqId = crypto.randomUUID();
    const rpcParams = { messages, maxTokens, includeContext };
    if (systemPrompt != null) rpcParams.systemPrompt = systemPrompt;
    if (temperature != null) rpcParams.temperature = temperature;
    if (stopSequences) rpcParams.stopSequences = stopSequences;
    if (modelPreferences) rpcParams.modelPreferences = modelPreferences;
    if (metadata) rpcParams.metadata = metadata;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this._pending.delete(reqId)) {
          reject(
            new SamplingError(
              SAMPLING_ERR_TIMEOUT,
              `sampling/createMessage timed out after ${timeoutMs}ms`
            )
          );
        }
      }, timeoutMs);
      this._pending.set(reqId, { resolve, reject, timer });

      try {
        this._writeFrame({
          jsonrpc: "2.0",
          id: reqId,
          method: METHOD_SAMPLING_CREATE_MESSAGE,
          params: rpcParams,
        });
      } catch (err) {
        clearTimeout(timer);
        this._pending.delete(reqId);
        reject(err);
      }
    });
  }

  /**
   * Try to resolve the matching pending request from a parsed JSON-RPC
   * frame. Returns true if `msg` was a response we owned and it was
   * resolved/rejected accordingly.
   *
   * @param {object} msg — already JSON-parsed.
   * @returns {boolean}
   */
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
        new SamplingError(
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

module.exports = {
  SamplingClient,
  SamplingError,
  PROTOCOL_VERSION_V1,
  PROTOCOL_VERSION_V2,
  METHOD_INITIALIZE,
  METHOD_SHUTDOWN,
  METHOD_SAMPLING_CREATE_MESSAGE,
  SAMPLING_ERR_NOT_GRANTED,
  SAMPLING_ERR_QUOTA_EXCEEDED,
  SAMPLING_ERR_PROVIDER_ERROR,
  SAMPLING_ERR_INVALID_REQUEST,
  SAMPLING_ERR_TIMEOUT,
  SAMPLING_ERR_MAX_CALLS_EXCEEDED,
  SAMPLING_ERR_MAX_TOKENS_EXCEEDED,
  SAMPLING_ERR_NOT_NEGOTIATED,
  SAMPLING_ERR_USER_DENIED,
};
