/**
 * discovery.js — platform runtime discovery (forum #490 contract).
 *
 * Resolution order (§4.8.4 of the design):
 *   1. contract      — ANNA_RUNTIMES_JSON / ANNA_CHROME_* / ANNA_NODE_* env
 *   2. legacy-scan   — scan PLAYWRIGHT_BROWSERS_PATH (or the Playwright
 *                      per-OS default cache dir) for chromium-<rev>; NOT a
 *                      contract (directory names are Playwright internals),
 *                      kept so the demo renders on not-yet-upgraded Agents.
 *   3. null          — nothing found; `advice` explains what to do.
 */
"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function chromeVersionFromBinary(executable) {
  try {
    const out = execFileSync(executable, ["--version"], {
      encoding: "utf-8",
      timeout: 10_000,
    });
    const m = out.match(/(\d+\.\d+\.\d+\.\d+)/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

function defaultBrowsersPath() {
  const env = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (env && env !== "0") return env;
  const home = os.homedir();
  switch (process.platform) {
    case "darwin":
      return path.join(home, "Library", "Caches", "ms-playwright");
    case "win32":
      return path.join(
        process.env.LOCALAPPDATA || path.join(home, "AppData", "Local"),
        "ms-playwright",
      );
    default:
      return path.join(
        process.env.XDG_CACHE_HOME || path.join(home, ".cache"),
        "ms-playwright",
      );
  }
}

/** Per-OS candidate executable paths inside a chromium-<rev> directory. */
function chromiumCandidates(revDir) {
  switch (process.platform) {
    case "darwin":
      return [
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
      ].map((p) => path.join(revDir, p));
    case "win32":
      return [
        path.join(revDir, "chrome-win64", "chrome.exe"),
        path.join(revDir, "chrome-win", "chrome.exe"),
      ];
    default:
      return [
        path.join(revDir, "chrome-linux64", "chrome"),
        path.join(revDir, "chrome-linux", "chrome"),
      ];
  }
}

/** 2) legacy-scan: highest chromium-<rev> under the browsers dir. */
function legacyScanChrome() {
  const root = defaultBrowsersPath();
  let entries;
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return null;
  }
  const revs = entries
    .filter((e) => e.isDirectory() && /^chromium-\d+$/.test(e.name))
    .map((e) => ({ name: e.name, rev: Number(e.name.split("-")[1]) }))
    .sort((a, b) => b.rev - a.rev);
  for (const { name } of revs) {
    for (const candidate of chromiumCandidates(path.join(root, name))) {
      if (fs.existsSync(candidate)) {
        return {
          executable: candidate,
          version: chromeVersionFromBinary(candidate),
          provider: "playwright-cft",
          source: "legacy-scan",
        };
      }
    }
  }
  return null;
}

function parseRuntimesJson() {
  const raw = process.env.ANNA_RUNTIMES_JSON;
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Discover what this Agent provides.
 *
 * @returns {{
 *   contractPresent: boolean,
 *   profile: string|null,
 *   agentVersion: string|null,
 *   chrome: {executable:string, version:string|null, provider:string, source:string, usable:boolean}|null,
 *   node: {executable:string, version:string, provider:string, source:string}|null,
 *   advice: string[],
 * }}
 */
function discover() {
  const advice = [];
  const runtimesJson = parseRuntimesJson();
  const contractPresent = Boolean(
    runtimesJson
      || process.env.ANNA_CHROME_EXECUTABLE
      || process.env.ANNA_NODE_EXECUTABLE,
  );

  // ---- chrome -----------------------------------------------------------
  let chrome = null;
  if (process.env.ANNA_CHROME_EXECUTABLE) {
    chrome = {
      executable: process.env.ANNA_CHROME_EXECUTABLE,
      version:
        process.env.ANNA_CHROME_VERSION
        || (runtimesJson && runtimesJson.chrome && runtimesJson.chrome.version)
        || null,
      provider: process.env.ANNA_CHROME_PROVIDER || "playwright-cft",
      source: "contract",
    };
  } else {
    chrome = legacyScanChrome();
    if (chrome) {
      advice.push(
        "Chrome was found by scanning PLAYWRIGHT_BROWSERS_PATH (legacy-scan). "
          + "That directory layout is a Playwright implementation detail, not a "
          + "contract — upgrade the Agent to get ANNA_CHROME_EXECUTABLE.",
      );
    }
  }
  if (chrome) chrome.usable = fs.existsSync(chrome.executable);

  // ---- node -------------------------------------------------------------
  let node = null;
  if (process.env.ANNA_NODE_EXECUTABLE) {
    node = {
      executable: process.env.ANNA_NODE_EXECUTABLE,
      version:
        process.env.ANNA_NODE_VERSION
        || (runtimesJson && runtimesJson.node && runtimesJson.node.version)
        || "unknown",
      provider: "anna-shared",
      source: "contract",
    };
  }

  // ---- advice -----------------------------------------------------------
  if (!contractPresent) {
    advice.push(
      "This Agent predates the platform runtime contract (no ANNA_RUNTIMES_JSON). "
        + "Upgrade Matrix to use ANNA_CHROME_EXECUTABLE / ANNA_NODE_EXECUTABLE.",
    );
  }
  if (!chrome) {
    advice.push(
      "No Chrome found. On desktop, install the browser from the Matrix "
        + "Dashboard; on cloud the image should always provide it.",
    );
  }
  if (!node) {
    advice.push(
      "No platform Node.js. Dev mode runs on your own node — expected. On a "
        + "Phase 2 Agent, install the shared Node once from the agent console "
        + "('Platform' row → install); runtime.requires.node then gates the "
        + "install with a clear runtime_missing error until it is provisioned.",
    );
  }

  return {
    contractPresent,
    profile: process.env.ANNA_RUNTIME_PROFILE || null,
    agentVersion: process.env.ANNA_AGENT_VERSION || null,
    chrome,
    node,
    advice,
  };
}

module.exports = { discover, legacyScanChrome, defaultBrowsersPath, chromeVersionFromBinary };
