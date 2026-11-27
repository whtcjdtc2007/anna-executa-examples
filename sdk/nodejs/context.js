/**
 * Invoke context — typed view of ``params.context`` for a single tool
 * invocation. Mirrors :mod:`executa_sdk.context` in the Python SDK so
 * dual-language plugins share the same mental model.
 *
 * The host (matrix Agent) propagates ``deadline_ms`` (Unix epoch
 * milliseconds, derived from the invoke ``timeoutMs``) into
 * ``params.context.deadline_ms``. Older hosts will simply omit it, in
 * which case :func:`remainingS` returns ``Number.POSITIVE_INFINITY``.
 *
 * Example
 * -------
 *
 *   const { InvokeContext } = require("@anna/executa-sdk");
 *
 *   async function handleInvoke(request) {
 *     const ctx = InvokeContext.fromParams(request.params);
 *     if (ctx.expired()) {
 *       return _err(req.id, SUBCALL_TIMEOUT, "no time left in budget");
 *     }
 *     // Tighten the next reverse-RPC. The host loader auto-injects
 *     // ``_clientTimeoutS`` from ``ctx.deadline_ms`` when missing, but
 *     // explicit is friendlier when you want a shorter slice than
 *     // "all remaining time".
 *     await storage.set(key, value, {
 *       timeoutMs: Math.min(5000, ctx.remainingS() * 1000),
 *     });
 *   }
 */

class InvokeContext {
  /**
   * @param {object} [opts]
   * @param {string|null} [opts.invokeId]
   * @param {string|null} [opts.pluginName]
   * @param {number|null} [opts.deadlineMs]
   * @param {object|null} [opts.credentials]
   * @param {object|null} [opts.raw]
   */
  constructor({
    invokeId = null,
    pluginName = null,
    deadlineMs = null,
    credentials = null,
    raw = null,
  } = {}) {
    this.invokeId = invokeId;
    this.pluginName = pluginName;
    this.deadlineMs = deadlineMs;
    this.credentials = credentials;
    this.raw = raw;
    Object.freeze(this);
  }

  /**
   * Build an :class:`InvokeContext` from the raw ``params`` object of an
   * ``invoke`` JSON-RPC request.
   *
   * @param {object|null|undefined} params
   * @returns {InvokeContext}
   */
  static fromParams(params) {
    if (!params || typeof params !== "object") return new InvokeContext();
    const ctx =
      params.context && typeof params.context === "object" ? params.context : {};
    let deadlineInt = null;
    if (ctx.deadline_ms != null) {
      const n = Number(ctx.deadline_ms);
      if (Number.isFinite(n)) deadlineInt = Math.trunc(n);
    }
    const credentials =
      ctx.credentials && typeof ctx.credentials === "object"
        ? ctx.credentials
        : null;
    return new InvokeContext({
      invokeId: ctx.invoke_id ?? params.invoke_id ?? null,
      pluginName: ctx.plugin_name ?? null,
      deadlineMs: deadlineInt,
      credentials,
      raw: ctx && Object.keys(ctx).length > 0 ? ctx : null,
    });
  }

  /**
   * Seconds left in the invoke budget.
   * Returns ``Number.POSITIVE_INFINITY`` when no deadline was sent.
   *
   * @returns {number}
   */
  remainingS() {
    if (this.deadlineMs == null) return Number.POSITIVE_INFINITY;
    return Math.max(0, this.deadlineMs / 1000 - Date.now() / 1000);
  }

  /** @returns {boolean} */
  hasDeadline() {
    return this.deadlineMs != null;
  }

  /** True iff a deadline is set and has already passed. */
  expired() {
    return this.hasDeadline() && this.remainingS() <= 0;
  }
}

// ─── Current-invoke propagation (reverse-RPC correlation) ─────────────
//
// The host Agent associates every reverse RPC (host/uploadFile,
// storage/*, image/*, sampling/createMessage, …) with its parent
// ``invoke`` via ``params.context.invoke_id``. When a plugin handles
// multiple invokes CONCURRENTLY, omitting the field forces the host to
// guess — which intermittently attributes the reverse RPC to the wrong
// invoke (forum #188: negotiate/confirm r2_key ownership failures).
//
// Wrap your tool handler in :func:`bindInvoke` and every SDK client
// automatically stamps outgoing reverse RPCs:
//
//   const { bindInvoke } = require("@anna/executa-sdk");
//
//   async function handleInvoke(reqId, params) {
//     return bindInvoke(params, async () => {
//       ... // any SDK reverse-RPC call made here is correlated
//     });
//   }
//
// Outside any invoke. A reverse RPC issued with nothing bound carries no
// ``invoke_id``; the host serves it with the Executa's background
// credentials (process start, timers, work that continues after an invoke
// returned). Async work started inside a handler INHERITS that handler's
// binding, so work meant to outlive its invoke must detach explicitly:
//
//   function startLongRunningWork() {
//     bindInvoke(null, async () => {
//       ... // reverse RPCs here are background, not the old invoke
//     });
//   }

const { AsyncLocalStorage } = require("node:async_hooks");

const _currentInvoke = new AsyncLocalStorage();

/**
 * Run `fn` with the current invoke bound (AsyncLocalStorage scope).
 *
 * @template T
 * @param {object|string|null} paramsOrInvokeId — the raw `invoke` request
 *   params (invoke_id extracted via `InvokeContext.fromParams`) or an
 *   invoke_id string.
 * @param {() => T} fn
 * @returns {T}
 */
function bindInvoke(paramsOrInvokeId, fn) {
  const invokeId =
    typeof paramsOrInvokeId === "string" || paramsOrInvokeId == null
      ? paramsOrInvokeId || null
      : InvokeContext.fromParams(paramsOrInvokeId).invokeId;
  return _currentInvoke.run(invokeId, fn);
}

/** @returns {string|null} the invoke_id bound to the current async scope. */
function getCurrentInvokeId() {
  return _currentInvoke.getStore() ?? null;
}

/**
 * Stamp `params.context.invoke_id` from the current binding. Used by
 * every SDK reverse-RPC client just before writing the request frame.
 * No-op when nothing is bound or the caller already set it. Mutates and
 * returns `params`.
 *
 * @param {object} params
 * @returns {object}
 */
function attachInvokeContext(params) {
  const invokeId = getCurrentInvokeId();
  if (!invokeId || !params || typeof params !== "object") return params;
  if (!params.context || typeof params.context !== "object") {
    params.context = {};
  }
  if (params.context.invoke_id == null) {
    params.context.invoke_id = invokeId;
  }
  return params;
}

module.exports = {
  InvokeContext,
  bindInvoke,
  getCurrentInvokeId,
  attachInvokeContext,
};
