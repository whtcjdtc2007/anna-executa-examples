#!/usr/bin/env node
/**
 * focus-session — Executa stdio tool plugin (Node.js flavour)
 *
 * Mirrors the Python implementation in ../focus-session-python so the same
 * Anna App bundle can talk to either runtime without UI changes.
 *
 * Persists Pomodoro / deep-work sessions to ~/.anna/focus-flow/state.json
 * (same path as the Python version — they share state, but only one should
 * be enabled at a time per the app manifest).
 *
 * Protocol: JSON-RPC 2.0 over stdio (newline-delimited)
 * Methods : describe, invoke, health
 *
 * IMPORTANT — tool_id minting:
 *   Both `MANIFEST.name` and the matching entry in package.json `bin` MUST
 *   equal the tool_id minted at https://anna.partners/executa. The shared
 *   sibling script `../../scripts/set-tool-id.py` (used by the Python flavour)
 *   only rewrites the Python files; for this Node flavour you must update
 *   MANIFEST.name + package.json `bin` + executa.json `tool_id` by hand.
 */

"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { randomUUID } = require("node:crypto");

// ---------------------------------------------------------------------------
// Plugin manifest — Anna calls `describe` and uses this dict verbatim.
// ---------------------------------------------------------------------------
const MANIFEST = {
  name: "tool-test-focus-session-12345678",
  display_name: "Focus Session",
  version: "1.0.0",
  description:
    "Pomodoro / deep-work session timer. State persists to "
    + "~/.anna/focus-flow/state.json.",
  author: "Acme Labs",
  homepage: "https://github.com/openclaw/anna-executa-examples",
  license: "MIT",
  tags: ["productivity", "focus", "pomodoro", "anna-app"],
  tools: [
    {
      name: "session",
      description:
        "Manage a focus session. Use the `action` parameter to select an "
        + "operation: start | pause | resume | complete | get_state.",
      parameters: [
        {
          name: "action",
          type: "string",
          description:
            "One of: start, pause, resume, complete, get_state.",
          required: true,
        },
        {
          name: "duration_minutes",
          type: "integer",
          description: "Required when action='start'. 1-180 minutes.",
          required: false,
        },
        {
          name: "topic",
          type: "string",
          description: "Optional label for action='start' (max 120 chars).",
          required: false,
          default: "",
        },
        {
          name: "notes",
          type: "string",
          description:
            "Optional reflection for action='complete' (max 500 chars).",
          required: false,
          default: "",
        },
      ],
    },
  ],
  runtime: { type: "node", min_version: "18.0.0" },
};

// ---------------------------------------------------------------------------
// State persistence
// ---------------------------------------------------------------------------
const STATE_DIR = path.join(os.homedir(), ".anna", "focus-flow");
const STATE_FILE = path.join(STATE_DIR, "state.json");
const MAX_HISTORY = 200;

function now() {
  return Date.now() / 1000;
}

function loadState() {
  if (!fs.existsSync(STATE_FILE)) return { active: null, history: [] };
  try {
    const data = JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
    if (typeof data !== "object" || data === null) {
      throw new Error("state.json root must be an object");
    }
    if (!("active" in data)) data.active = null;
    if (!("history" in data)) data.history = [];
    return data;
  } catch (err) {
    const backup = STATE_FILE.replace(
      /\.json$/,
      `.broken.${Math.floor(now())}.json`,
    );
    try {
      fs.renameSync(STATE_FILE, backup);
      process.stderr.write(
        `[focus-session] corrupt state moved to ${backup}: ${err.message}\n`,
      );
    } catch {
      /* ignore */
    }
    return { active: null, history: [] };
  }
}

function saveState(state) {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  const tmp = STATE_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, STATE_FILE);
}

function todayTotals(history) {
  const todayStartMs = new Date(new Date().toDateString()).getTime();
  const todayStart = todayStartMs / 1000;
  const today = (history || []).filter(
    (h) => (h.completed_at || 0) >= todayStart,
  );
  const seconds = today.reduce(
    (s, h) => s + (Number(h.focused_seconds) || 0),
    0,
  );
  return {
    session_count: today.length,
    focused_minutes: Math.round((seconds / 60) * 10) / 10,
    focused_seconds: seconds,
  };
}

function focusedSeconds(active) {
  if (!active) return 0;
  let acc = Number(active.accumulated_seconds || 0);
  if (active.status === "running") {
    acc += Math.floor(now() - Number(active.running_since || now()));
  }
  return Math.max(0, acc);
}

function activeView(active) {
  if (!active) return null;
  const view = { ...active };
  const focused = focusedSeconds(active);
  view.focused_seconds = focused;
  view.remaining_seconds = Math.max(
    0,
    Math.floor(Number(active.duration_seconds || 0) - focused),
  );
  return view;
}

// ---------------------------------------------------------------------------
// Action implementations
// ---------------------------------------------------------------------------
function actionStart({ duration_minutes, topic }) {
  if (duration_minutes == null) {
    throw new Error("duration_minutes is required for action='start'");
  }
  const dm = Number(duration_minutes);
  if (!Number.isInteger(dm) || dm < 1 || dm > 180) {
    throw new Error("duration_minutes must be an integer between 1 and 180");
  }
  const cleanTopic = String(topic || "").trim().slice(0, 120);
  const state = loadState();
  const t = now();
  state.active = {
    id: randomUUID().replace(/-/g, ""),
    topic: cleanTopic,
    duration_seconds: dm * 60,
    started_at: t,
    running_since: t,
    accumulated_seconds: 0,
    status: "running",
  };
  saveState(state);
  return { active: activeView(state.active) };
}

function actionPause() {
  const state = loadState();
  const active = state.active;
  if (!active) return { active: null, message: "No active session to pause." };
  if (active.status === "running") {
    active.accumulated_seconds = focusedSeconds(active);
    active.status = "paused";
    active.running_since = null;
    saveState(state);
  }
  return { active: activeView(active) };
}

function actionResume() {
  const state = loadState();
  const active = state.active;
  if (!active) return { active: null, message: "No active session to resume." };
  if (active.status !== "running") {
    active.status = "running";
    active.running_since = now();
    saveState(state);
  }
  return { active: activeView(active) };
}

function actionComplete({ notes }) {
  const state = loadState();
  const active = state.active;
  if (!active) return { completed: null, message: "No active session." };
  const focused = focusedSeconds(active);
  const record = {
    id: active.id,
    topic: active.topic || "",
    duration_seconds: active.duration_seconds || 0,
    focused_seconds: focused,
    started_at: active.started_at,
    completed_at: now(),
    notes: String(notes || "").trim().slice(0, 500),
  };
  const history = state.history || [];
  history.unshift(record);
  state.history = history.slice(0, MAX_HISTORY);
  state.active = null;
  saveState(state);
  return { completed: record, today: todayTotals(state.history) };
}

function actionGetState() {
  const state = loadState();
  const recent = (state.history || []).slice(0, 10);
  return {
    active: activeView(state.active),
    today: todayTotals(state.history || []),
    recent,
  };
}

function toolSession(args) {
  const action = args.action;
  switch (action) {
    case "start":
      return actionStart(args);
    case "pause":
      return actionPause();
    case "resume":
      return actionResume();
    case "complete":
      return actionComplete(args);
    case "get_state":
      return actionGetState();
    default:
      throw new Error(
        `unknown action: ${JSON.stringify(action)}; expected one of `
          + "start | pause | resume | complete | get_state",
      );
  }
}

const TOOL_DISPATCH = { session: toolSession };

// ---------------------------------------------------------------------------
// JSON-RPC handlers
// ---------------------------------------------------------------------------
function handleDescribe() {
  return MANIFEST;
}

function handleInvoke(params) {
  const tool = params.tool;
  const args =
    params.arguments && typeof params.arguments === "object"
      ? params.arguments
      : {};
  const fn = TOOL_DISPATCH[tool];
  if (!fn) throw new Error(`unknown tool: ${JSON.stringify(tool)}`);
  // The Executa runtime expects InvokeResult shape: {success, data} on
  // success, {success:false, error} on tool-layer failure.
  try {
    const payload = fn(args);
    return { success: true, data: payload };
  } catch (err) {
    return { success: false, error: `${err.name}: ${err.message}` };
  }
}

function handleHealth() {
  return { status: "ok", state_file: STATE_FILE };
}

const METHOD_DISPATCH = {
  describe: handleDescribe,
  invoke: handleInvoke,
  health: handleHealth,
};

// ---------------------------------------------------------------------------
// Stdio loop
// ---------------------------------------------------------------------------
function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function main() {
  process.stderr.write(
    `[focus-session] ${MANIFEST.display_name} v${MANIFEST.version} ready (node)\n`,
  );
  const rl = readline.createInterface({ input: process.stdin });
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
    const reqId = req.id;
    const method = req.method;
    const params = req.params || {};
    const handler = METHOD_DISPATCH[method];
    if (!handler) {
      send({
        jsonrpc: "2.0",
        id: reqId,
        error: { code: -32601, message: `method not found: ${method}` },
      });
      return;
    }
    try {
      const result = handler(params);
      send({ jsonrpc: "2.0", id: reqId, result });
    } catch (err) {
      send({
        jsonrpc: "2.0",
        id: reqId,
        error: { code: -32000, message: err.message || String(err) },
      });
    }
  });
}

main();
