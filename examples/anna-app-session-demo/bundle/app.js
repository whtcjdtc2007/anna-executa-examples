// Agent Session Demo — workspace file-editing best practices over
// anna.agent.session.* (forum /t/174 path determinism, /t/86 zero-tools,
// /t/191 empty completions + working cancel).
//
// The five practices demonstrated:
//   1. PIN the session to one filesystem client (submode "fixed" +
//      fixed_client_id) so every run targets the same workspace root.
//   2. DISCOVER the workspace root first; afterwards every instruction
//      embeds ABSOLUTE paths under it — never assume "~" or a relative CWD.
//   3. VERIFY writes with a nonce read-back in an independent run; compare
//      in app code. A "task completed" claim is not proof the file changed.
//   4. STOP on structured tool errors (error_code PATH_OUTSIDE_SANDBOX /
//      NOT_FOUND + sandbox_root) — never fall back to a similar path.
//   5. CLASSIFY the terminal state of every run: consume in-run error
//      frames ({event:"sse", error, error_type}) — empty_completion is a
//      retryable infrastructure error, NOT a business failure — and treat
//      task_cancelled as a distinct outcome. session.cancel(run_id) really
//      stops queued and running runs; session.delete cancels all of them.
//
// This bundle is loaded as a native ES module and imports the Anna App
// Runtime SDK directly. `.connect()` performs the host handshake.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const $ = (id) => document.getElementById(id);
const errBox = $("errors");
const uuidInput = $("session-uuid-input");
const statusEl = $("session-status");
const lifecycleEl = $("session-lifecycle");
const toolsEl = $("session-tools");
const toolsWarnEl = $("tools-warning");
const sessionListEl = $("session-list");
const submodeSel = $("submode-sel");
const fixedRow = $("fixed-client-row");
const rootStatusEl = $("root-status");
const writeTargetEl = $("write-target");
const verdictEl = $("verify-verdict");

const SUBMODE_HINTS = {
  auto:
    "Host resolves the agent (user default / cloud). Fine for text tasks; for file editing prefer 'fixed' so reads and writes cannot silently switch to a different machine's workspace mid-session.",
  fixed:
    "All runs in this session are pinned to ONE client_id — the strongest guarantee that every fs operation targets the same workspace root (forum /t/174). Requires the manifest grant agent.session.fixed (this app declares it).",
};

// ── Session-level system prompt: the path-discipline floor every
//    file-editing app should ship. Applied to every run in the session
//    (a per-run systemPrompt would override it for that turn only).
const DEFAULT_SYSTEM_PROMPT = `You are a workspace file assistant. Path discipline (MANDATORY):
1. Always call filesystem tools with ABSOLUTE paths, exactly as given. Never trim a path to a relative one, never substitute a root you assume is correct.
2. If a filesystem tool fails (e.g. error_code PATH_OUTSIDE_SANDBOX or NOT_FOUND), report the structured error VERBATIM (including path and sandbox_root when present) and STOP. Never retry with a rewritten path and never fall back to a similarly named file or directory.
3. When asked to reply with JSON, output ONLY the JSON object on a single line — no markdown fences, no commentary.`;

// Unified session state. The uuid box is the single source of truth so the
// user can act on ANY session (created, typed in, or picked from list()).
const sess = { handle: null, runId: null, root: null, writtenPath: null };

const currentUuid = () => (uuidInput.value || "").trim();

const annaReady = (async () => {
  const anna = await AnnaAppRuntime.connect();
  window.anna = anna;
  return anna;
})().catch((err) => {
  showError("runtime.connect", err);
  throw err;
});

function showError(label, err) {
  const code = (err && (err.errorType || err.code || err.error?.code)) || "unknown";
  const name = (err && (err.name || err.error?.name)) || "";
  const msg = (err && (err.message || err.error?.message)) || String(err);
  let hint = "";
  if (name === "APP_SESSION_EXPIRED" || name === "APP_SESSION_REVOKED") {
    hint = " — session is gone; create a new one.";
  } else if (name === "APP_SESSION_TOKEN_EXPIRED") {
    hint = " — token lapsed; click refresh or just retry.";
  } else if (err && err.errorType === "empty_completion") {
    hint = " — infrastructure hiccup (model returned nothing); retry or pick another model.";
  } else if (name === "APP_CONCURRENCY_LIMIT" || (err && err.code === "APP_CONCURRENCY_LIMIT")) {
    hint = " — you already have the max number of concurrent agent runs (10/user); wait for one to finish or cancel it, then retry.";
  }
  errBox.textContent = `[${label}] ${name || code}: ${msg}${hint}`;
}

const clearError = () => { errBox.textContent = "(none)"; };

// ── lifecycle + runtime-accurate tool surface ─────────────────────────────

function renderLifecycle(info) {
  const i = info || {};
  const parts = [];
  if (i.expires_at) parts.push(`idle-expires ${i.expires_at}`);
  if (i.max_lifetime_at) parts.push(`hard-cap ${i.max_lifetime_at}`);
  if (i.idle_ttl_seconds != null) parts.push(`idle-ttl ${i.idle_ttl_seconds}s`);
  lifecycleEl.textContent = parts.length ? `lifecycle: ${parts.join("  ·  ")}` : "lifecycle: (none)";
}

// session.create / run_meta carry the tools the session can REALLY execute.
// For a file-editing app this check is NOT optional: with zero tools the
// agent is text-only and every claimed side effect is hallucinated.
function renderToolSurface(info) {
  if (!info || !Array.isArray(info.granted_tools)) {
    toolsEl.textContent = "tools: (unknown — run or re-create to find out)";
    toolsWarnEl.hidden = true;
    return;
  }
  const inherit = info.inherit_host_tools ?? info.granted_tools.includes("*");
  if (inherit) {
    toolsEl.textContent = "tools: * — inherits the user's host tools; file edits are REAL";
    toolsWarnEl.hidden = true;
  } else if (info.granted_tools.length) {
    toolsEl.textContent = `tools: ${info.granted_tools.join(", ")} (sandbox allow-list)`;
    toolsWarnEl.hidden = true;
  } else {
    toolsEl.textContent = "tools: NONE — text-only sandbox";
    toolsWarnEl.textContent =
      "NO_TOOLS_AVAILABLE: this session resolved ZERO executable tools. The walkthrough " +
      "below cannot touch real files — any write/verify output would be hallucinated. " +
      "Enable “Let agent sessions use my tools” in the app's grants drawer, then re-create.";
    toolsWarnEl.hidden = false;
  }
}

function handleRunMeta(frame, outEl) {
  renderToolSurface(frame);
  const tools = frame.inherit_host_tools
    ? "* (host tools)"
    : (frame.granted_tools || []).join(", ") || "NONE";
  const model = frame.model ? ` model=${frame.model}` : "";
  outEl.textContent += `[run_meta] run=${frame.run_id || "?"} submode=${frame.submode || "?"} tools=${tools}${model}\n`;
  for (const w of frame.warnings || []) {
    outEl.textContent += `⚠️ ${w.code}: ${w.message}\n`;
  }
}

// ── session plumbing ──────────────────────────────────────────────────────

function syncButtons() {
  const active = currentUuid().length > 0;
  for (const id of ["refresh-btn", "delete-btn", "history-btn", "run-btn", "cancel-btn", "discover-btn", "probe-btn"]) {
    $(id).disabled = !active;
  }
  // write needs a discovered root; verify additionally needs a written path.
  $("write-btn").disabled = !active || !sess.root;
  $("verify-btn").disabled = !active || !sess.writtenPath;
  statusEl.textContent = active ? `session: ${currentUuid()}` : "no session";
  for (const b of sessionListEl.querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.uuid === currentUuid());
  }
}

uuidInput.addEventListener("input", () => {
  if (sess.handle && sess.handle.app_session_uuid !== currentUuid()) {
    sess.handle = null;
    sess.runId = null;
    renderToolSurface(null);
  }
  syncButtons();
});

function setUuid(uuid) {
  uuidInput.value = uuid || "";
  syncButtons();
}

// Re-binds an existing uuid via the SDK's client-side attach() when the
// handle in memory doesn't match (typed-in uuid, list() pick, reload).
async function hostHandle() {
  const anna = await annaReady;
  const uuid = currentUuid();
  if (sess.handle && sess.handle.app_session_uuid === uuid) return sess.handle;
  sess.handle = anna.agent.session.attach(uuid);
  return sess.handle;
}

submodeSel.addEventListener("change", () => {
  $("submode-hint").textContent = SUBMODE_HINTS[submodeSel.value] || "";
  fixedRow.hidden = submodeSel.value !== "fixed";
});

$("create-btn").addEventListener("click", async () => {
  clearError();
  try {
    const anna = await annaReady;
    const submode = submodeSel.value;
    const req = {
      submode,
      system_prompt: ($("session-system-prompt").value || "").trim() || undefined,
      label: "session-demo",
    };
    if (submode === "fixed") {
      const cid = ($("fixed-client-input").value || "").trim();
      if (!cid) throw new Error("fixed_client_id required for submode=fixed");
      req.fixed_client_id = cid;
    }
    sess.handle = await anna.agent.session(req);
    // New session → new workspace context: drop step state from any prior one.
    sess.root = null;
    sess.writtenPath = null;
    rootStatusEl.textContent = "workspace root: (unknown)";
    writeTargetEl.textContent = "target: (discover the root first)";
    verdictEl.hidden = true;
    setUuid(sess.handle.app_session_uuid || "");
    renderLifecycle(sess.handle);
    renderToolSurface(sess.handle);
    $("setup-out").textContent = JSON.stringify(
      {
        app_session_uuid: sess.handle.app_session_uuid,
        submode,
        fixed_client_id: req.fixed_client_id || null,
        granted_tools: sess.handle.granted_tools,
        inherit_host_tools: sess.handle.inherit_host_tools,
      },
      null,
      2,
    );
  } catch (err) {
    showError("agent.session.create", err);
  }
});

$("refresh-btn").addEventListener("click", async () => {
  clearError();
  try {
    const handle = await hostHandle();
    const r = await handle.refresh();
    renderLifecycle(handle);
    $("setup-out").textContent = JSON.stringify(r, null, 2);
  } catch (err) {
    showError("agent.session.refresh", err);
  }
});

$("delete-btn").addEventListener("click", async () => {
  clearError();
  try {
    const handle = await hostHandle();
    // delete revokes the session AND cancels all of its active runs
    // (queued runs are dropped, running runs stop at the next checkpoint)
    // — no need to cancel() each run_id first for a clean teardown.
    await handle.delete();
    sess.handle = null;
    sess.runId = null;
    setUuid("");
    renderLifecycle(null);
    renderToolSurface(null);
    $("setup-out").textContent = "(session deleted — active runs cancelled)";
  } catch (err) {
    showError("agent.session.delete", err);
  }
});

$("history-btn").addEventListener("click", async () => {
  clearError();
  try {
    const handle = await hostHandle();
    $("setup-out").textContent = JSON.stringify(await handle.history(), null, 2);
  } catch (err) {
    showError("agent.session.history", err);
  }
});

$("list-btn").addEventListener("click", async () => {
  clearError();
  try {
    const anna = await annaReady;
    const r = await anna.agent.session.list({ include_expired: false, limit: 50 });
    const sessions = (r && r.sessions) || r || [];
    sessionListEl.replaceChildren();
    for (const s of sessions) {
      const uuid = s.app_session_uuid || s.uuid;
      if (!uuid) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.uuid = uuid;
      const tail = uuid.length > 14 ? `…${uuid.slice(-10)}` : uuid;
      btn.textContent = s.label ? `${s.label} (${tail})` : tail;
      btn.title = uuid;
      btn.addEventListener("click", () => {
        sess.handle = null;
        sess.runId = null;
        setUuid(uuid);
        renderLifecycle(s);
      });
      sessionListEl.appendChild(btn);
    }
    $("setup-out").textContent = JSON.stringify(sessions, null, 2);
    syncButtons();
  } catch (err) {
    showError("agent.session.list", err);
  }
});

// ── shared run helper ─────────────────────────────────────────────────────

// A run that ends without any output/tool activity is surfaced by the host
// as an in-run error frame with this error_type (forum /t/191). It means
// the upstream model returned an empty completion — infrastructure, not
// "the agent chose to do nothing" — so the right app reaction is retry /
// switch model, NOT a business-level failure like "file not modified".
const EMPTY_COMPLETION = "empty_completion";

class RunError extends Error {
  constructor(message, { errorType, code, cancelled = false } = {}) {
    super(message);
    this.name = "RunError";
    this.errorType = errorType || null; // in-run: quota_exhausted, empty_completion, …
    this.code = code || null;           // gate/infra: queue_timeout, session_revoked, …
    this.cancelled = cancelled;
  }
}

// Streams one run into outEl (prompt echoed first) and resolves with the
// concatenated token text — the walkthrough steps parse JSON out of it.
// Classifies EVERY way a run can terminate (practice 5):
//   • assistant text        → collected + returned
//   • {event:"sse", error}  → RunError with errorType (empty_completion …)
//   • {event:"error"}       → RunError with code (queue_timeout …)
//   • delta.task_cancelled  → RunError with cancelled=true
//   • delta.task_complete   → usage line (proof the run really billed work)
async function runAndCollect(prompt, outEl) {
  const handle = await hostHandle();
  outEl.textContent = `>>> prompt:\n${prompt}\n\n<<< reply:\n`;
  let text = "";
  const stream = handle.run({ content: prompt });
  for await (const frame of stream) {
    if (frame.run_id) sess.runId = frame.run_id;
    if (frame.event === "queued" || frame.event === "started") {
      outEl.textContent += `[${frame.event}]\n`;
    } else if (frame.event === "run_meta") {
      handleRunMeta(frame, outEl);
    } else if (frame.event === "sse") {
      // In-run error frames ride the sse envelope: {error, error_type}.
      if (frame.error) {
        throw new RunError(frame.error, { errorType: frame.error_type });
      }
      // Token frames carry an OpenAI-style chunk — streamed text lives at
      // choices[0].delta.content. (There is no {event:"token"} frame on
      // the real host; see docs llm-and-agent.md.)
      const delta = frame.choices?.[0]?.delta;
      if (typeof delta?.content === "string" && delta.content) {
        text += delta.content;
        outEl.textContent += delta.content;
      } else if (delta?.task_cancelled) {
        throw new RunError(delta.task_cancelled.reason || "run cancelled", {
          cancelled: true,
        });
      } else if (delta?.task_complete) {
        const u = delta.task_complete.token_usage;
        const m = delta.task_complete.model ? ` model=${delta.task_complete.model}` : "";
        if (u) outEl.textContent += `\n[usage] tokens=${u.total_tokens ?? "?"}${m}`;
      }
    } else if (frame.event === "error") {
      throw new RunError(frame.message || "run error", { code: frame.code });
    }
  }
  if (stream.runId) sess.runId = stream.runId;
  outEl.textContent += "\n(done)";
  return text;
}

// Extract the last JSON object from model text. Tolerates markdown fences
// and prose around the JSON (models don't always follow "JSON only").
function extractJson(text) {
  const cleaned = text.replace(/```(?:json)?/gi, "");
  for (let start = cleaned.indexOf("{"); start !== -1; start = cleaned.indexOf("{", start + 1)) {
    for (let end = cleaned.lastIndexOf("}"); end > start; end = cleaned.lastIndexOf("}", end - 1)) {
      try {
        return JSON.parse(cleaned.slice(start, end + 1));
      } catch {
        /* keep shrinking */
      }
    }
  }
  return null;
}

// Join an absolute root and a relative tail without doubling slashes.
const joinPath = (root, tail) => `${root.replace(/\/+$/, "")}/${tail}`;

// ── Step 1: discover the workspace root ──────────────────────────────────

const DISCOVER_PROMPT = `Report the canonical ABSOLUTE path of the workspace root your filesystem tools operate on (list it with your directory tool and use the exact absolute path from the tool response — do not guess from your home directory). Reply with ONLY this JSON on one line: {"workspace_root": "<canonical absolute path>"}`;

$("discover-btn").addEventListener("click", async () => {
  clearError();
  try {
    const text = await runAndCollect(DISCOVER_PROMPT, $("discover-out"));
    const j = extractJson(text);
    const root = j && typeof j.workspace_root === "string" ? j.workspace_root.trim() : "";
    if (root && root.startsWith("/")) {
      sess.root = root;
      rootStatusEl.textContent = `workspace root: ${root}`;
      writeTargetEl.textContent = `target: ${joinPath(root, `session-demo/proof-${nonce()}.txt`)}`;
    } else {
      sess.root = null;
      rootStatusEl.textContent = "workspace root: (could not parse an absolute path — see output)";
    }
    syncButtons();
  } catch (err) {
    showError("walkthrough.discover", err);
  }
});

// ── Step 2: write + nonce-verified read-back ──────────────────────────────

const nonce = () => ($("nonce-input").value || "").trim() || "demo-nonce-0001";

$("nonce-random-btn").addEventListener("click", () => {
  $("nonce-input").value = `nonce-${Math.random().toString(36).slice(2, 10)}`;
  if (sess.root) {
    writeTargetEl.textContent = `target: ${joinPath(sess.root, `session-demo/proof-${nonce()}.txt`)}`;
  }
  // A new nonce invalidates any previous write/verify pairing.
  sess.writtenPath = null;
  verdictEl.hidden = true;
  syncButtons();
});

$("write-btn").addEventListener("click", async () => {
  clearError();
  verdictEl.hidden = true;
  try {
    if (!sess.root) throw new Error("run discover first — write instructions must embed the real absolute root");
    const target = joinPath(sess.root, `session-demo/proof-${nonce()}.txt`);
    writeTargetEl.textContent = `target: ${target}`;
    const prompt =
      `Create the file at this EXACT absolute path: ${target}\n` +
      `The file content must be exactly this single line: ${nonce()}\n` +
      `Use the absolute path verbatim — do not rewrite it or choose another directory. ` +
      `On success reply with ONLY this JSON: {"written": "<the absolute path from the tool response>"}. ` +
      `If the write fails, reply with the tool's structured error as JSON, verbatim.`;
    const text = await runAndCollect(prompt, $("write-out"));
    const j = extractJson(text);
    // Track what the agent CLAIMS it wrote; step 2b will prove or refute it.
    sess.writtenPath = (j && typeof j.written === "string" && j.written.trim()) || target;
    syncButtons();
  } catch (err) {
    showError("walkthrough.write", err);
  }
});

$("verify-btn").addEventListener("click", async () => {
  clearError();
  try {
    if (!sess.writtenPath) throw new Error("run write first");
    const prompt =
      `Read the file back at this EXACT absolute path: ${sess.writtenPath}\n` +
      `Reply with ONLY this JSON: {"path": "<absolute path>", "content": "<exact file content>"}. ` +
      `If the read fails, reply with the tool's structured error as JSON, verbatim. ` +
      `Never search other directories for a similarly named file.`;
    const text = await runAndCollect(prompt, $("write-out"));
    const j = extractJson(text);
    const got = j && typeof j.content === "string" ? j.content.trim() : null;
    const pass = got !== null && got.includes(nonce());
    verdictEl.hidden = false;
    verdictEl.classList.toggle("ok", pass);
    verdictEl.classList.toggle("warn", !pass);
    verdictEl.textContent = pass
      ? `✅ VERIFIED — the read-back content contains the nonce "${nonce()}". The write really happened at ${sess.writtenPath}.`
      : `❌ NOT VERIFIED — the read-back did not return the nonce "${nonce()}". Treat the write as NOT done, regardless of what the previous run claimed. Inspect the output (structured error? wrong path? zero tools?).`;
  } catch (err) {
    showError("walkthrough.verify", err);
  }
});

// ── Step 3: out-of-root probe ─────────────────────────────────────────────

const PROBE_PATH = "/session-demo-out-of-root-probe/probe.txt";
const PROBE_PROMPT =
  `Attempt to read the file at this EXACT absolute path, exactly once: ${PROBE_PATH}\n` +
  `This path is intentionally outside your workspace and is expected to fail. ` +
  `Reply with ONLY the tool's error response as JSON, verbatim — include error_code, path, and sandbox_root if present. ` +
  `Do NOT retry with a different path, do NOT search for the file elsewhere, do NOT read any other file instead.`;

$("probe-btn").addEventListener("click", async () => {
  clearError();
  try {
    const text = await runAndCollect(PROBE_PROMPT, $("probe-out"));
    const j = extractJson(text);
    if (j && j.error_code) {
      $("probe-out").textContent +=
        `\n\n[app] structured error surfaced: error_code=${j.error_code}` +
        (j.sandbox_root ? ` sandbox_root=${j.sandbox_root}` : "") +
        " — this is the correct stop-and-report behaviour.";
    }
  } catch (err) {
    showError("walkthrough.probe", err);
  }
});

// ── freeform run / cancel / empty-completion handling ─────────────────────

// Practice 5 in action: one retry on empty_completion (an infrastructure
// hiccup — the model returned nothing), and cancellation rendered as a
// distinct outcome instead of an error. Anything else is a real error.
$("run-btn").addEventListener("click", async () => {
  clearError();
  const outEl = $("run-out");
  const prompt = $("run-input").value || "hello";
  for (let attempt = 1; ; attempt++) {
    try {
      await runAndCollect(prompt, outEl);
      return;
    } catch (err) {
      if (err instanceof RunError && err.cancelled) {
        outEl.textContent += `\n\n[cancelled] ${err.message} — the host stopped the run (task_cancelled + end frames). Not an error.`;
        return;
      }
      if (err instanceof RunError && err.errorType === EMPTY_COMPLETION && attempt === 1) {
        outEl.textContent += `\n\n[${EMPTY_COMPLETION}] ${err.message}\n[app] retryable infrastructure error — retrying once…\n`;
        continue;
      }
      showError("agent.session.run", err);
      return;
    }
  }
});

// session.cancel(run_id) is honoured server-side: a still-queued run is
// dropped before it starts; a running run stops at the next poll checkpoint
// (an in-flight provider call finishes first). {cancelled:true} is the ack
// that the signal was recorded — watch the STREAM for task_cancelled + end
// to confirm the actual stop.
$("cancel-btn").addEventListener("click", async () => {
  clearError();
  try {
    const handle = await hostHandle();
    const res = await handle.cancel(sess.runId || undefined);
    $("run-out").textContent +=
      `\n\n[cancel] ${JSON.stringify(res)} — signal recorded; the run's stream terminates with task_cancelled + end.`;
  } catch (err) {
    showError("agent.session.cancel", err);
  }
});

// ── init ──────────────────────────────────────────────────────────────────

$("session-system-prompt").value = DEFAULT_SYSTEM_PROMPT;
$("submode-hint").textContent = SUBMODE_HINTS[submodeSel.value] || "";
fixedRow.hidden = submodeSel.value !== "fixed";
syncButtons();
