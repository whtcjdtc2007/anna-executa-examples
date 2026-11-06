#!/usr/bin/env node
/**
 * runtime-probe — Executa stdio tool plugin for the platform runtime demo
 * (forum #490). Three tools:
 *
 *   probe_runtimes  — what does THIS Agent provide? (contract / legacy / none)
 *   check_requires  — preview an install-time runtime.requires precheck
 *   render_html     — HTML → PNG/PDF via the PLATFORM Chrome (puppeteer-core)
 *
 * Protocol requirements:
 *   - stdin:  one JSON-RPC request object per line
 *   - stdout: one JSON-RPC response object per line (NOTHING else!)
 *   - stderr: diagnostics only
 *   - long-running: loop until stdin EOF
 *   - methods: describe / invoke / health; `initialize` intentionally
 *     answers -32601 so the Agent downgrades to protocol v1 (this demo
 *     needs no reverse RPC).
 */
"use strict";

const readline = require("node:readline");

const { discover } = require("./lib/discovery.js");
const { checkRequires, isValidRange } = require("./lib/requires.js");
const { renderHtml } = require("./lib/render.js");

const MANIFEST = {
  name: "runtime-probe",
  display_name: "Platform Runtime Probe",
  version: "1.0.0",
  description:
    "Probe the Anna platform shared runtimes (Node.js / Chrome), preview "
    + "runtime.requires install prechecks, and render HTML to PNG/PDF with "
    + "the platform Chrome — shipping zero browser bytes.",
  tools: [
    {
      name: "probe_runtimes",
      description:
        "Report which platform runtimes this Agent provides, how they were "
        + "discovered (contract | legacy-scan), and upgrade advice.",
      parameters: [],
    },
    {
      name: "check_requires",
      description:
        "Evaluate a runtime.requires map against this Agent exactly like the "
        + "install-time precheck would (chrome compares major only).",
      parameters: [
        {
          name: "node",
          type: "string",
          description: 'semver range for node, e.g. ">=22 <23"',
          required: false,
        },
        {
          name: "chrome",
          type: "string",
          description: 'semver range for chrome (major only), e.g. ">=140"',
          required: false,
        },
      ],
    },
    {
      name: "render_html",
      description:
        "Render HTML to PNG or PDF using the platform Chrome. Writes into "
        + "$ANNA_WORKSPACE_DIR/platform-runtime-demo/.",
      parameters: [
        { name: "html", type: "string", description: "HTML (≤ 512 KiB); default demo card", required: false },
        { name: "title", type: "string", description: "title for the default HTML", required: false },
        { name: "format", type: "string", description: '"png" (default) | "pdf"', required: false },
        { name: "width", type: "number", description: "viewport width (default 1280)", required: false },
        { name: "height", type: "number", description: "viewport height (default 720)", required: false },
        { name: "full_page", type: "boolean", description: "PNG: capture full page", required: false },
      ],
    },
  ],
  runtime: { type: "node", min_version: "20.0.0" },
};

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

function toolProbeRuntimes() {
  const d = discover();
  const runningNode = {
    executable: process.execPath,
    version: process.versions.node,
  };
  const contractEnv = {};
  for (const key of [
    "ANNA_RUNTIME_PROFILE",
    "ANNA_AGENT_VERSION",
    "ANNA_CHROME_EXECUTABLE",
    "ANNA_CHROME_VERSION",
    "ANNA_CHROME_PROVIDER",
    "ANNA_NODE_EXECUTABLE",
    "ANNA_NODE_VERSION",
    "ANNA_RUNTIMES_JSON",
  ]) {
    if (process.env[key] !== undefined) contractEnv[key] = process.env[key];
  }
  const contextEnv = {};
  for (const key of ["ANNA_WORKSPACE_DIR", "EXECUTA_HOME", "EXECUTA_DATA"]) {
    if (process.env[key] !== undefined) contextEnv[key] = process.env[key];
  }
  return {
    profile: d.profile,
    agent_version: d.agentVersion,
    contract_present: d.contractPresent,
    chrome: d.chrome,
    node: {
      platform: d.node,
      running: runningNode,
      is_platform_node:
        Boolean(d.node) && d.node.executable === process.execPath,
    },
    env: { contract: contractEnv, context: contextEnv },
    advice: d.advice,
  };
}

function toolCheckRequires(args) {
  const requires = {};
  if (args.node !== undefined) requires.node = String(args.node);
  if (args.chrome !== undefined) requires.chrome = String(args.chrome);
  if (Object.keys(requires).length === 0) {
    requires.node = ">=22 <23";
    requires.chrome = ">=140";
  }
  for (const [key, range] of Object.entries(requires)) {
    if (!isValidRange(range)) {
      throw new Error(
        `invalid semver range for ${key}: ${JSON.stringify(range)} `
          + "(supported: > >= < <= = ^ ~, space AND, || OR)",
      );
    }
  }
  const d = discover();
  const found = {
    node: d.node ? d.node.version : null,
    chrome: d.chrome ? d.chrome.version : null,
  };
  return { requires, ...checkRequires(requires, found) };
}

const TOOL_DISPATCH = {
  probe_runtimes: toolProbeRuntimes,
  check_requires: toolCheckRequires,
  render_html: renderHtml, // async
};

// ---------------------------------------------------------------------------
// JSON-RPC plumbing (describe / invoke / health)
// ---------------------------------------------------------------------------

function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

async function handleInvoke(params) {
  const tool = params.tool;
  const args =
    params.arguments && typeof params.arguments === "object"
      ? params.arguments
      : {};
  const fn = TOOL_DISPATCH[tool];
  if (!fn) throw new Error(`unknown tool: ${JSON.stringify(tool)}`);
  try {
    const payload = await fn(args);
    if (payload && payload.success === false) {
      // Structured tool-level failure (render_html error codes).
      return { success: false, error: payload.error, data: payload };
    }
    return { success: true, data: payload };
  } catch (err) {
    return { success: false, error: `${err.name}: ${err.message}` };
  }
}

function handleHealth() {
  const d = discover();
  return {
    status: "ok",
    contract_present: d.contractPresent,
    chrome_version: d.chrome ? d.chrome.version : null,
    node_version: d.node ? d.node.version : null,
  };
}

async function dispatch(req) {
  const { id, method } = req;
  const params = req.params || {};
  try {
    switch (method) {
      case "describe":
        return { jsonrpc: "2.0", id, result: MANIFEST };
      case "invoke":
        return { jsonrpc: "2.0", id, result: await handleInvoke(params) };
      case "health":
        return { jsonrpc: "2.0", id, result: handleHealth() };
      default:
        // includes `initialize` → Agent falls back to protocol v1.
        return {
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `method not found: ${method}` },
        };
    }
  } catch (err) {
    return {
      jsonrpc: "2.0",
      id,
      error: { code: -32000, message: err.message || String(err) },
    };
  }
}

function main() {
  process.stderr.write(
    `[runtime-probe] ${MANIFEST.display_name} v${MANIFEST.version} ready `
      + `(node ${process.versions.node}, contract=${Boolean(process.env.ANNA_RUNTIMES_JSON)})\n`,
  );
  const rl = readline.createInterface({ input: process.stdin });
  // Serialise requests: render_html drives a whole Chrome — never in parallel.
  let chain = Promise.resolve();
  rl.on("line", (raw) => {
    const line = raw.trim();
    if (!line) return;
    let req;
    try {
      req = JSON.parse(line);
    } catch (e) {
      send({
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: `parse error: ${e.message}` },
      });
      return;
    }
    chain = chain.then(async () => {
      send(await dispatch(req));
    });
  });
}

main();
