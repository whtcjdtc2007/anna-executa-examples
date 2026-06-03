// LLM Demo — exercises anna.llm.complete + anna.agent.session.* surfaces.
//
// This bundle is loaded as a native ES module (`<script src="app.js"
// type="module">`), so it imports the Anna App Runtime SDK directly below.
// Call `.connect()` to do the host handshake and obtain the `anna` instance
// that has .llm / .agent / etc.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Must match the manifest's required_executas[].tool_id and the
// Executa plugin's MANIFEST.name (see executas/llm-via-executa-python/).
const EXECUTA_TOOL_ID = "tool-test-llm-via-executa-12345678";
const EXECUTA_METHOD = "complete";

const MODE_HINTS = {
  direct:
    "Calls the host LLM directly from the iframe (anna.llm.complete).",
  executa:
    "Invokes the Executa, which then asks the host to sample (sampling/createMessage). Requires --real LLM bridge; mock fixtures do not serve reverse sampling.",
};

const $ = (id) => document.getElementById(id);
const out = $("complete-out");
const runOut = $("run-out");
const errBox = $("errors");
const sessionUuidEl = $("session-uuid");
const modeSel = $("llm-mode");
const modeHint = $("mode-hint");

let session = null;

// Bootstrap the runtime. All click handlers await `annaReady` so they never
// touch `window.anna` before the handshake completes.
const annaReady = (async () => {
  const anna = await AnnaAppRuntime.connect();
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
// 1. completion — either anna.llm.complete (direct) or
//    anna.tools.invoke against the llm-via-executa plugin which
//    in turn issues sampling/createMessage to the host.

modeSel?.addEventListener("change", () => {
  modeHint.textContent = MODE_HINTS[modeSel.value] || "";
});

async function runDirect(prompt) {
  const anna = await annaReady;
  return anna.llm.complete({
    messages: [{ role: "user", content: { type: "text", text: prompt } }],
    maxTokens: 256,
  });
}

async function runViaExecuta(prompt) {
  const anna = await annaReady;
  return anna.tools.invoke({
    tool_id: EXECUTA_TOOL_ID,
    method: EXECUTA_METHOD,
    args: { prompt, max_tokens: 256 },
  });
}

$("complete-btn").addEventListener("click", async () => {
  clearError();
  const mode = modeSel?.value || "direct";
  out.textContent = `(calling ${mode === "executa" ? "executa.complete" : "llm.complete"}…)`;
  try {
    const prompt = $("complete-input").value || "hi";
    const reply =
      mode === "executa" ? await runViaExecuta(prompt) : await runDirect(prompt);
    out.textContent = JSON.stringify(reply, null, 2);
  } catch (err) {
    out.textContent = "(failed)";
    showError(mode === "executa" ? "tools.invoke" : "llm.complete", err);
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
