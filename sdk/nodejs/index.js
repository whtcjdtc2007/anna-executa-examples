/**
 * @anna/executa-sdk — Node.js helpers for Executa plugins.
 *
 * Currently exposes only the SamplingClient — see ./sampling.js.
 */

const sampling = require("./sampling");

module.exports = {
  ...sampling,
};
