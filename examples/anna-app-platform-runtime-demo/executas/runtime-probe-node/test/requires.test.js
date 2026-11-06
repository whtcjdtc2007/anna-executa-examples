/**
 * Unit tests for lib/requires.js — run with `node --test`.
 *
 * Mirrors the matrix Agent test matrix (tests/unit/test_platform_runtimes.py)
 * so the demo's preview precheck can never drift from the real one.
 */
"use strict";

const assert = require("node:assert");
const test = require("node:test");

const { checkRequires, isValidRange, satisfies } = require("../lib/requires.js");

test("satisfies: >=22 <23", () => {
  assert.equal(satisfies("22.14.0", ">=22 <23"), true);
  assert.equal(satisfies("23.0.0", ">=22 <23"), false);
  assert.equal(satisfies("21.9.9", ">=22 <23"), false);
});

test("satisfies: caret / tilde", () => {
  assert.equal(satisfies("22.1.3", "^22"), true);
  assert.equal(satisfies("23.0.0", "^22"), false);
  assert.equal(satisfies("22.1.5", "~22.1"), true);
  assert.equal(satisfies("22.2.0", "~22.1"), false);
});

test("satisfies: OR clauses", () => {
  assert.equal(satisfies("18.19.0", ">=22 <23 || ^18"), true);
  assert.equal(satisfies("20.0.0", ">=22 <23 || ^18"), false);
});

test("satisfies: bare version matches at declared precision", () => {
  assert.equal(satisfies("22.5.1", "22"), true);
  assert.equal(satisfies("23.0.0", "22"), false);
  assert.equal(satisfies("22.14.0", "22.14.0"), true);
});

test("satisfies: chrome major-only comparison", () => {
  assert.equal(satisfies("145.0.7632.12", ">=140", { majorOnly: true }), true);
  assert.equal(satisfies("145.0.7632.12", ">=146", { majorOnly: true }), false);
  assert.equal(satisfies("146.0.7680.31", "<146", { majorOnly: true }), false);
  assert.equal(satisfies("145.0.7632.12", ">=140 <146", { majorOnly: true }), true);
});

test("satisfies: missing version never satisfies", () => {
  assert.equal(satisfies(null, ">=22"), false);
  assert.equal(satisfies(undefined, ">=22"), false);
  assert.equal(satisfies("", ">=22"), false);
});

test("isValidRange", () => {
  for (const ok of [">=22 <23", "^22", "~22.1", ">=22 <23 || ^18", "22.14.0"]) {
    assert.equal(isValidRange(ok), true, ok);
  }
  for (const bad of ["", "latest", ">=foo", "22; rm -rf /", null, 42]) {
    assert.equal(isValidRange(bad), false, String(bad));
  }
});

test("checkRequires: mirrors the Agent failure metadata", () => {
  const result = checkRequires(
    { node: ">=22 <23", chrome: ">=140" },
    { node: null, chrome: "145.0.7632.12" },
  );
  assert.equal(result.satisfied, false);
  assert.deepEqual(result.missing_runtimes, {
    node: "required >=22 <23, found none",
  });
  assert.equal(result.checks.node.ok, false);
  assert.equal(result.checks.chrome.ok, true);
  assert.equal(result.checks.chrome.compare, "major-only");
});

test("checkRequires: satisfied + unknown keys are conservative", () => {
  const ok = checkRequires(
    { node: ">=22 <23" },
    { node: "22.14.0", chrome: null },
  );
  assert.equal(ok.satisfied, true);

  const unknown = checkRequires({ ffmpeg: ">=6" }, { node: "22.14.0", chrome: null });
  assert.equal(unknown.satisfied, false);
  assert.deepEqual(unknown.unknown_keys, ["ffmpeg"]);
});
