"use strict";
// SamplingClient.createMessage param forwarding. Run: node --test sdk/nodejs/test
const test = require("node:test");
const assert = require("node:assert/strict");

const { SamplingClient, METHOD_SAMPLING_CREATE_MESSAGE } = require("../sampling");

const MESSAGES = [{ role: "user", content: { type: "text", text: "hi" } }];
const RESULT = {
  role: "assistant",
  content: { type: "text", text: "ok" },
  model: "m",
  stopReason: "endTurn",
  usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
};

async function roundtrip(extra) {
  const frames = [];
  const client = new SamplingClient({ writeFrame: (m) => frames.push(m) });
  const p = client.createMessage({ messages: MESSAGES, maxTokens: 16, ...extra });
  await new Promise((r) => setImmediate(r));
  const env = frames[0];
  assert.equal(env.method, METHOD_SAMPLING_CREATE_MESSAGE);
  client.dispatchResponse({ jsonrpc: "2.0", id: env.id, result: RESULT });
  assert.deepEqual(await p, RESULT);
  return env.params;
}

test("thinking omitted when undefined", async () => {
  const params = await roundtrip({});
  assert.equal("thinking" in params, false);
  assert.equal("responseFormat" in params, false);
});

test("thinking=false forwarded verbatim", async () => {
  const params = await roundtrip({ thinking: false });
  assert.equal(params.thinking, false);
});

test("thinking object forwarded verbatim", async () => {
  const spec = { enabled: true, effort: "low", budgetTokens: 1024, includeReasoning: true };
  const params = await roundtrip({ thinking: spec });
  assert.deepEqual(params.thinking, spec);
});

test("responseFormat forwarded verbatim", async () => {
  const params = await roundtrip({ responseFormat: { type: "json_object" }, onUnsupported: "text" });
  assert.deepEqual(params.responseFormat, { type: "json_object" });
  assert.equal(params.onUnsupported, "text");
});
