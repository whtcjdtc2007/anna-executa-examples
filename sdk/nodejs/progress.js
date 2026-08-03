/**
 * Plugin → host progress notifications for long-running tool jobs.
 *
 * Design: matrix-nexus docs/design/anna-app-tools-invoke-async-jobs.md §4.2 /
 * Phase E. Mirrors `executa_sdk.progress.emit_progress` in the Python SDK —
 * keep the two in sync.
 *
 * When an app invokes your tool via `anna.tools.invokeAsync`, the host runs
 * it as a long job and relays progress to the app UI. Call `emitProgress`
 * from inside your tool handler:
 *
 *   const { emitProgress } = require("@anna/executa-sdk");
 *
 *   async function handleInvoke(req) {
 *     const ctx = InvokeContext.fromParams(req.params);
 *     for (let i = 0; i < total; i++) {
 *       await doStep(i);
 *       emitProgress("tool_update", { step: i + 1, total },
 *                    { invokeId: ctx.invokeId });
 *     }
 *   }
 *
 * Wire shape — a JSON-RPC NOTIFICATION (no `id`; the host never responds):
 *
 *   {"jsonrpc":"2.0","method":"executa/progress",
 *    "params":{"type":"tool_update","data":{...},
 *              "context":{"invoke_id":"<parent invoke>"}}}
 *
 * Host-side semantics (silent drop by design):
 *  - `type` must be "progress" | "tool_update" — others coerced to
 *    "progress" (terminal states cannot be faked);
 *  - unknown / finished invoke ids are dropped;
 *  - rate limit 50 events/second per invoke — excess dropped;
 *  - only ASYNC job invokes (tools.invokeAsync) have a progress channel;
 *  - keep `data` small (host stores ≤8KB per event).
 */

"use strict";

const METHOD_EXECUTA_PROGRESS = "executa/progress";
const PROGRESS_TYPES = new Set(["progress", "tool_update"]);

/**
 * Publish one progress event for the given invoke. Best-effort: returns
 * `true` if the notification was written, `false` when skipped (missing
 * invokeId / serialization failure). Never throws.
 *
 * @param {string} type "progress" | "tool_update"
 * @param {object} [data] small JSON payload, e.g. {step, total, note}
 * @param {object} opts
 * @param {string} opts.invokeId REQUIRED — parent invoke id from
 *   `InvokeContext.fromParams(req.params).invokeId`.
 * @param {NodeJS.WritableStream} [opts.stdout] test seam.
 * @returns {boolean}
 */
function emitProgress(type, data, { invokeId, stdout } = {}) {
  if (!invokeId) return false;
  const t = PROGRESS_TYPES.has(type) ? type : "progress";
  const frame = {
    jsonrpc: "2.0",
    method: METHOD_EXECUTA_PROGRESS,
    params: {
      type: t,
      data: data && typeof data === "object" ? data : {},
      context: { invoke_id: invokeId },
    },
  };
  try {
    (stdout || process.stdout).write(JSON.stringify(frame) + "\n");
    return true;
  } catch (_e) {
    return false;
  }
}

module.exports = { emitProgress, METHOD_EXECUTA_PROGRESS };
