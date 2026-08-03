/**
 * @anna/executa-sdk — Node.js helpers for Executa plugins.
 *
 * Exposes:
 *   - SamplingClient (./sampling.js)
 *   - StorageClient + FilesClient (./storage.js) for Anna Persistent Storage
 *   - InvokeContext (./context.js) — typed view of params.context with
 *     remainingS() / expired() helpers honouring the host deadline_ms.
 *   - emitProgress (./progress.js) — long-job progress notifications for
 *     the tools.invokeAsync channel.
 *   - makeResponseRouter helper to multiplex stdin frames across clients.
 */

const sampling = require("./sampling");
const storage = require("./storage");
const image = require("./image");
const hostUpload = require("./host_upload");
const web = require("./web");
const context = require("./context");
const progress = require("./progress");

module.exports = {
  ...sampling,
  ...storage,
  ...image,
  ...hostUpload,
  ...web,
  ...context,
  ...progress,
};
