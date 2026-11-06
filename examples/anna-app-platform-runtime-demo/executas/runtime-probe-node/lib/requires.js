/**
 * requires.js — runtime.requires range evaluation.
 *
 * The exact same semver-range subset the Agent evaluates at install time
 * (matrix `src/executa/platform_runtimes.py`): comparators `> >= < <= = ^ ~`,
 * whitespace = AND, `||` = OR. Chrome compares the MAJOR version only.
 * Zero dependencies; unit tests in ../test/requires.test.js (`node --test`).
 */
"use strict";

const COMPARATOR_RE = /^(>=|<=|>|<|=|\^|~)?\s*v?\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$/;

/** Parse the first three numeric components, ignoring pre-release/build. */
function parseVersion(v) {
  const core = String(v).trim().replace(/^v/, "").split(/[-+]/)[0];
  const nums = core.split(".").slice(0, 3).map((p) => {
    const n = Number.parseInt(p, 10);
    return Number.isNaN(n) ? 0 : n;
  });
  while (nums.length < 3) nums.push(0);
  return nums;
}

function cmp(a, b) {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return 0;
}

function comparatorSatisfied(found, op, targetRaw, majorOnly) {
  const target = parseVersion(targetRaw);
  const precision = targetRaw
    .replace(/^v/, "")
    .split(/[-+]/)[0]
    .split(".")
    .filter(Boolean).length;
  const lhs = majorOnly ? [found[0], 0, 0] : found;
  const rhs = majorOnly ? [target[0], 0, 0] : target;
  switch (op) {
    case "^": {
      let upper;
      if (target[0] > 0) upper = [target[0] + 1, 0, 0];
      else if (target[1] > 0) upper = [0, target[1] + 1, 0];
      else upper = [0, 0, target[2] + 1];
      return cmp(found, target) >= 0 && cmp(found, upper) < 0;
    }
    case "~": {
      const upper = precision >= 2 ? [target[0], target[1] + 1, 0] : [target[0] + 1, 0, 0];
      return cmp(found, target) >= 0 && cmp(found, upper) < 0;
    }
    case "":
    case "=": {
      if (majorOnly) return found[0] === target[0];
      // Bare version matches at its declared precision: "22" ≡ 22.x.x
      for (let i = 0; i < Math.min(precision, 3); i++) {
        if (found[i] !== target[i]) return false;
      }
      return true;
    }
    case ">":
      return cmp(lhs, rhs) > 0;
    case ">=":
      return cmp(lhs, rhs) >= 0;
    case "<":
      return cmp(lhs, rhs) < 0;
    case "<=":
      return cmp(lhs, rhs) <= 0;
    default:
      throw new Error(`unsupported comparator: ${op}${targetRaw}`);
  }
}

/** True when `version` satisfies `range`. `null`/missing version never does. */
function satisfies(version, range, opts = {}) {
  if (!version || typeof range !== "string" || !range.trim()) return false;
  const majorOnly = Boolean(opts.majorOnly);
  const found = parseVersion(version);
  for (const clause of range.split("||")) {
    const comparators = clause.trim().split(/\s+/).filter(Boolean);
    if (comparators.length === 0) continue;
    let ok = true;
    for (let comp of comparators) {
      comp = comp.trim();
      let op = "";
      for (const candidate of [">=", "<=", ">", "<", "=", "^", "~"]) {
        if (comp.startsWith(candidate)) {
          op = candidate;
          comp = comp.slice(candidate.length).trim();
          break;
        }
      }
      if (!comparatorSatisfied(found, op, comp, majorOnly)) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

/** Validate range syntax against the supported subset. */
function isValidRange(range) {
  if (typeof range !== "string" || !range.trim()) return false;
  for (const clause of range.split("||")) {
    const comparators = clause.trim().split(/\s+/).filter(Boolean);
    if (comparators.length === 0) return false;
    for (const comp of comparators) {
      if (!COMPARATOR_RE.test(comp.trim())) return false;
    }
  }
  return true;
}

const SUPPORTED_KEYS = new Set(["node", "chrome"]);

/**
 * Evaluate a requires map against detected runtime versions.
 * Output mirrors the Agent's install-time failure metadata so the UI can
 * render exactly what a real install would report.
 *
 * @param {Record<string,string>} requires e.g. {node: ">=22 <23", chrome: ">=140"}
 * @param {{node?: string|null, chrome?: string|null}} found detected versions
 */
function checkRequires(requires, found) {
  const checks = {};
  const missing = {};
  const unknownKeys = [];
  for (const [key, range] of Object.entries(requires || {})) {
    if (!SUPPORTED_KEYS.has(key)) {
      unknownKeys.push(key);
      missing[key] = `required ${range}, found none (unsupported key)`;
      continue;
    }
    const majorOnly = key === "chrome";
    const version = found[key] || null;
    const ok = isValidRange(range) && satisfies(version, range, { majorOnly });
    checks[key] = {
      required: range,
      found: version || "none",
      ok,
      ...(majorOnly ? { compare: "major-only" } : {}),
    };
    if (!ok) missing[key] = `required ${range}, found ${version || "none"}`;
  }
  return {
    satisfied: Object.keys(missing).length === 0,
    checks,
    missing_runtimes: missing,
    unknown_keys: unknownKeys,
  };
}

module.exports = { parseVersion, satisfies, isValidRange, checkRequires };
