#!/usr/bin/env node
/**
 * sampling-tool.js — Executa plugin (Node.js) that uses host LLM sampling.
 *
 * Demonstrates:
 *   - the v2 `initialize` handshake (advertises client_capabilities.sampling)
 *   - issuing a reverse `sampling/createMessage` request to the host
 *   - sharing one stdin reader between agent invokes and host responses
 *
 * The plugin exposes a single tool `summarize` that asks the host LLM to
 * produce a short summary. The host owns model selection, billing, and
 * quota; this plugin never holds an LLM API key.
 *
 * To enable end-to-end:
 *   1. Declare host_capabilities: ["llm.sample"] in the published manifest.
 *   2. The user must toggle sampling_grant.enabled = true in Anna Admin.
 */

"use strict";

const path = require("node:path");
const readline = require("node:readline");

// Allow running directly from the repo without `npm install`.
const sdk = require(path.resolve(__dirname, "..", "..", "sdk", "nodejs"));
const {
  SamplingClient,
  SamplingError,
  PROTOCOL_VERSION_V2,
} = sdk;

// ─── Manifest ────────────────────────────────────────────────────────

const MANIFEST = {
  name: "sampling-summarizer-node",
  display_name: "Sampling Summarizer (Node.js)",
  version: "0.1.0",
  description: "Summarizes text by asking the host to sample an LLM.",
  author: "Anna Developer",
  // NEW in v2 — declares the reverse capabilities this plugin will use.
  host_capabilities: ["llm.sample"],
  tools: [
    {
      name: "summarize",
      description: "Summarize the supplied text into one short paragraph.",
      parameters: [
        { name: "text", type: "string", description: "Text to summarize", required: true },
        { name: "max_words", type: "integer", description: "Approx max words", required: false, default: 80 },
      ],
    },
  ],
  runtime: { type: "node", min_version: "18.0.0" },
};

// ─── Wiring ──────────────────────────────────────────────────────────

function writeFrame(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

const sampling = new SamplingClient({ writeFrame });

function makeResponse(id, { result, error } = {}) {
  const out = { jsonrpc: "2.0", id };
  if (error) out.error = error;
  else out.result = result;
  return out;
}

function handleInitialize(reqId, params) {
  const proto = (params && params.protocolVersion) || "1.1";
  if (proto !== PROTOCOL_VERSION_V2) {
    sampling.disable(
      `host did not negotiate v2 (offered protocolVersion=${proto}); ` +
        "sampling/createMessage requires Executa protocol 2.0"
    );
  }
  return makeResponse(reqId, {
    result: {
      protocolVersion: proto === PROTOCOL_VERSION_V2 ? "2.0" : "1.1",
      serverInfo: { name: MANIFEST.name, version: MANIFEST.version },
      client_capabilities: proto === PROTOCOL_VERSION_V2 ? { sampling: {} } : {},
      capabilities: {},
    },
  });
}

async function handleSummarize(args, invokeId) {
  const text = String(args.text || "");
  if (!text.trim()) {
    return { summary: "", note: "empty input" };
  }
  const maxWords = Math.max(20, Math.min(400, Number(args.max_words || 80)));
  const maxTokens = Math.max(64, Math.min(1024, maxWords * 5));

  const result = await sampling.createMessage({
    messages: [
      {
        role: "user",
        content: {
          type: "text",
          text:
            `Summarize the following text in at most ${maxWords} words. ` +
            "Return only the summary, no preamble.\n\n---\n" + text,
        },
      },
    ],
    maxTokens,
    systemPrompt: "You are a concise editorial assistant.",
    // No modelPreferences → host uses the user's preferred_model.
    metadata: { executa_invoke_id: invokeId, tool: "summarize" },
    timeoutMs: 60_000,
  });

  const content = result.content || {};
  return {
    summary: content && content.type === "text" ? content.text : "",
    model: result.model,
    usage: result.usage,
    stopReason: result.stopReason,
  };
}

async function handleInvoke(reqId, params) {
  const tool = params && params.tool;
  const args = (params && params.arguments) || {};
  const invokeId = (params && params.invoke_id) || "";

  if (tool !== "summarize") {
    return makeResponse(reqId, { error: { code: -32601, message: `Unknown tool: ${tool}` } });
  }
  try {
    const data = await handleSummarize(args, invokeId);
    return makeResponse(reqId, { result: { success: true, tool, data } });
  } catch (err) {
    if (err instanceof SamplingError) {
      return makeResponse(reqId, { error: { code: err.code, message: err.message, data: err.data } });
    }
    return makeResponse(reqId, { error: { code: -32603, message: `Tool execution failed: ${err.message}` } });
  }
}

async function handleMessage(line) {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    writeFrame(makeResponse(null, { error: { code: -32700, message: "Parse error" } }));
    return;
  }

  // Reverse-RPC reply from host → resolve a pending sampling promise.
  if (!("method" in msg)) {
    if (!sampling.dispatchResponse(msg)) {
      process.stderr.write(`⚠️  unmatched response id=${JSON.stringify(msg.id)}\n`);
    }
    return;
  }

  const { method, id: reqId } = msg;
  const params = msg.params || {};
  let resp;
  switch (method) {
    case "initialize": resp = handleInitialize(reqId, params); break;
    case "describe":   resp = makeResponse(reqId, { result: MANIFEST }); break;
    case "invoke":     resp = await handleInvoke(reqId, params); break;
    case "health":     resp = makeResponse(reqId, { result: { status: "healthy", version: MANIFEST.version } }); break;
    case "shutdown":   resp = makeResponse(reqId, { result: { ok: true } }); break;
    default:           resp = makeResponse(reqId, { error: { code: -32601, message: `Method not found: ${method}` } });
  }
  if (reqId != null) writeFrame(resp);
}

function main() {
  process.stderr.write("🔌 sampling-summarizer-node plugin started\n");
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    // Fire-and-forget — host invokes can be concurrent, and each may block
    // on a reverse RPC. Promises queue automatically.
    handleMessage(trimmed).catch((err) => {
      process.stderr.write(`handler error: ${err.stack || err}\n`);
    });
  });
  rl.on("close", () => process.exit(0));
}

main();
