/**
 * @anna/executa-sdk — Node.js helpers for Executa plugins.
 *
 * Exposes:
 *   - SamplingClient (./sampling.js)
 *   - StorageClient + FilesClient (./storage.js) for Anna Persistent Storage
 *   - makeResponseRouter helper to multiplex stdin frames across clients.
 */

const sampling = require("./sampling");
const storage = require("./storage");

module.exports = {
  ...sampling,
  ...storage,
};
