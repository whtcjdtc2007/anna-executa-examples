"use strict";
// ImageClient async-job surface (image-jobs §11.5). Run: node --test sdk/nodejs/test
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  ImageClient,
  ImageError,
  ImageJobTimeout,
  IMAGE_ERR_CONTENT_REJECTED,
  METHOD_IMAGE_GENERATE_ASYNC,
  METHOD_IMAGE_EDIT_ASYNC,
  METHOD_IMAGE_GET_JOB,
  METHOD_IMAGE_CANCEL_JOB,
  METHOD_IMAGE_RESIGN,
} = require("../image");

function host() {
  const frames = [];
  const client = new ImageClient({ writeFrame: (m) => frames.push(m) });
  const reply = (env, result, error) =>
    client.dispatchResponse(error ? { jsonrpc: "2.0", id: env.id, error } : { jsonrpc: "2.0", id: env.id, result });
  const tick = () => new Promise((r) => setImmediate(r));
  return { frames, client, reply, tick };
}

test("imageGenerateAsync envelope", async () => {
  const h = host();
  const p = h.client.imageGenerateAsync({
    prompt: "a cat", n: 2, size: "2048x1152", model: "fal-ai/nano-banana-2",
    params: { resolution: "2K" }, referenceImageUrls: ["https://r/a.png"], clientTag: "t", idempotencyKey: "inv:1",
  });
  await h.tick();
  const env = h.frames[0];
  assert.equal(env.method, METHOD_IMAGE_GENERATE_ASYNC);
  assert.deepEqual(env.params.params, { resolution: "2K" });
  assert.equal(env.params.fit, "nearest");
  assert.deepEqual(env.params.reference_image_urls, ["https://r/a.png"]);
  assert.equal(env.params.clientTag, "t");
  assert.equal(env.params.idempotencyKey, "inv:1");
  h.reply(env, { jobId: "ijob_x", state: "queued" });
  assert.equal((await p).jobId, "ijob_x");
});

test("edit/get/cancel/resign envelopes", async () => {
  const h = host();
  const p1 = h.client.imageEditAsync({ imageUrl: "https://in.png", prompt: "p", maskUrl: "https://m.png" });
  const p2 = h.client.imageGetJob("ijob_e");
  const p3 = h.client.imageCancelJob("ijob_e");
  const p4 = h.client.imageResign(["k1", "k2"]);
  await h.tick();
  const [e1, e2, e3, e4] = h.frames;
  assert.equal(e1.method, METHOD_IMAGE_EDIT_ASYNC);
  assert.equal(e1.params.image_url, "https://in.png");
  assert.equal(e1.params.mask_url, "https://m.png");
  assert.equal(e2.method, METHOD_IMAGE_GET_JOB); assert.equal(e2.params.jobId, "ijob_e");
  assert.equal(e3.method, METHOD_IMAGE_CANCEL_JOB);
  assert.equal(e4.method, METHOD_IMAGE_RESIGN); assert.deepEqual(e4.params.r2Keys, ["k1", "k2"]);
  h.reply(e1, { jobId: "ijob_e" }); h.reply(e2, { state: "running" }); h.reply(e3, { refunded: true });
  h.reply(e4, { urls: [{ r2Key: "k1" }, { r2Key: "k2" }] });
  assert.equal((await p1).jobId, "ijob_e");
  assert.equal((await p2).state, "running");
  assert.equal((await p3).refunded, true);
  assert.equal((await p4).urls.length, 2);
});

async function drive(h, promise, views) {
  let answered = 0;
  const next = async () => { for (let i = 0; i < 500 && h.frames.length <= answered; i++) await h.tick(); return h.frames[answered++]; };
  h.reply(await next(), { jobId: "ijob_w", state: "queued" });
  for (const v of views) { const env = await next(); assert.equal(env.method, METHOD_IMAGE_GET_JOB); h.reply(env, v); }
  return promise;
}

test("imageGenerateAwait succeeds with progress", async () => {
  const h = host();
  const progress = [];
  const out = await drive(h, h.client.imageGenerateAwait({ prompt: "x", pollIntervalMs: 1, awaitTimeoutMs: 1000, onProgress: (p) => progress.push(p) }), [
    { jobId: "ijob_w", state: "queued", progress: { phase: "IN_QUEUE", queuePosition: 2 } },
    { jobId: "ijob_w", state: "succeeded", result: { images: [{ r2Key: "k" }] } },
  ]);
  assert.equal(out.state, "succeeded");
  assert.deepEqual(progress.map((p) => p.phase), ["IN_QUEUE"]);
});

test("imageGenerateAwait failed → ImageError(content rejected)", async () => {
  const h = host();
  await assert.rejects(
    drive(h, h.client.imageGenerateAwait({ prompt: "x", pollIntervalMs: 1, awaitTimeoutMs: 1000 }), [
      { jobId: "ijob_w", state: "failed", error: { code: "IMAGE_CONTENT_REJECTED", message: "nsfw" } },
    ]),
    (e) => e instanceof ImageError && e.code === IMAGE_ERR_CONTENT_REJECTED && e.data.jobId === "ijob_w"
  );
});

test("imageGenerateAwait timeout carries jobId", async () => {
  const h = host();
  await assert.rejects(
    drive(h, h.client.imageGenerateAwait({ prompt: "x", pollIntervalMs: 1, awaitTimeoutMs: 2 }), [
      { jobId: "ijob_w", state: "running" }, { jobId: "ijob_w", state: "running" },
    ]),
    (e) => e instanceof ImageJobTimeout && e.jobId === "ijob_w"
  );
});

test("imageEditAwait submits edit_async", async () => {
  const h = host();
  await drive(h, h.client.imageEditAwait({ imageUrl: "https://in.png", prompt: "p", pollIntervalMs: 1, awaitTimeoutMs: 1000 }), [
    { jobId: "ijob_w", state: "succeeded", result: { images: [] } },
  ]);
  assert.equal(h.frames[0].method, METHOD_IMAGE_EDIT_ASYNC);
});
