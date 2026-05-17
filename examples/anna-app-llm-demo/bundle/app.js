// LLM Demo — exercises anna.llm.complete + anna.agent.session.* surfaces.
//
// The Anna App Runtime SDK (loaded via <script src="/static/anna-apps/_sdk/.../index.js">
// in index.html) exposes `window.AnnaAppRuntime`. Call `.connect()` to do the
// host handshake and obtain the `anna` instance that has .llm / .agent / etc.

"use strict";

const $ = (id) => document.getElementById(id);
const out = $("complete-out");
const runOut = $("run-out");
const errBox = $("errors");
const sessionUuidEl = $("session-uuid");

let session = null;

// Bootstrap the runtime. All click handlers await `annaReady` so they never
// touch `window.anna` before the handshake completes.
const annaReady = (async () => {
  if (!window.AnnaAppRuntime) {
    throw new Error(
      "AnnaAppRuntime global missing — SDK script failed to load",
    );
  }
  const anna = await window.AnnaAppRuntime.connect();
  window.anna = anna;
  return anna;
})().catch((err) => {
  showError("runtime.connect", err);
  throw err;
});

function showError(label, err) {
  const code = (err && (err.code || err.error?.code)) || "unknown";
  const msg = (err && (err.message || err.error?.message)) || String(err);
  errBox.textContent = `[${label}] ${code}: ${msg}`;
  errBox.classList.add("err");
}

function clearError() {
  errBox.textContent = "(none)";
}

// ──────────────────────────────────────────────────────────
// 1. llm.complete

$("complete-btn").addEventListener("click", async () => {
  clearError();
  out.textContent = "(calling llm.complete…)";
  try {
    const anna = await annaReady;
    const reply = await anna.llm.complete({
      messages: [
        {
          role: "user",
          content: { type: "text", text: $("complete-input").value || "hi" },
        },
      ],
      maxTokens: 256,
    });
    out.textContent = JSON.stringify(reply, null, 2);
  } catch (err) {
    out.textContent = "(failed)";
    showError("llm.complete", err);
  }
});

// ──────────────────────────────────────────────────────────
// 2. agent.session (auto)

$("session-create-btn").addEventListener("click", async () => {
  clearError();
  try {
    const anna = await annaReady;
    session = await anna.agent.session({ submode: "auto" });
    sessionUuidEl.textContent = session.appSessionUuid || "(no uuid?)";
    sessionUuidEl.classList.remove("muted");
    $("run-btn").disabled = false;
    $("history-btn").disabled = false;
    $("delete-btn").disabled = false;
  } catch (err) {
    showError("agent.session.create", err);
  }
});

$("run-btn").addEventListener("click", async () => {
  clearError();
  if (!session) return;
  runOut.textContent = "(streaming…)\n";
  try {
    const stream = session.run({ content: $("run-input").value || "hello" });
    for await (const frame of stream) {
      // frame.event ∈ "token" | "tool_call" | "tool_result" | "complete"
      if (frame.event === "token" && frame.text) {
        runOut.textContent += frame.text;
      } else {
        runOut.textContent += `\n[${frame.event}] ${JSON.stringify(frame)}\n`;
      }
    }
    runOut.textContent += "\n(done)";
  } catch (err) {
    showError("agent.session.run", err);
  }
});

$("history-btn").addEventListener("click", async () => {
  clearError();
  if (!session) return;
  try {
    const h = await session.history();
    runOut.textContent = JSON.stringify(h, null, 2);
  } catch (err) {
    showError("agent.session.history", err);
  }
});

$("delete-btn").addEventListener("click", async () => {
  clearError();
  if (!session) return;
  try {
    await session.delete();
    runOut.textContent = "(session deleted)";
    sessionUuidEl.textContent = "no session";
    sessionUuidEl.classList.add("muted");
    session = null;
    $("run-btn").disabled = true;
    $("history-btn").disabled = true;
    $("delete-btn").disabled = true;
  } catch (err) {
    showError("agent.session.delete", err);
  }
});
