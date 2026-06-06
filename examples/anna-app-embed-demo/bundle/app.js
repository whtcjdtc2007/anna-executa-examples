// Embed Demo — exercises anna.llm.embed + reverse embeddings/create.
//
// Two paths the user can switch between:
//   1. direct  → window.anna.llm.embed({input,model})
//   2. executa → window.anna.tools.invoke({tool_id, method:"embed", args:{texts,model}})
//                The Executa plugin (executas/embed-via-executa-python) then
//                calls embeddings/create reverse-RPC back to the host.
//
// Loaded as a native ES module (`<script src="app.js" type="module">`), so it
// imports the Anna App Runtime SDK directly below.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Must match manifest.required_executas[].tool_id and the
// executa.json "tool_id" in executas/embed-via-executa-python/.
const EXECUTA_TOOL_ID = "tool-test-embed-via-executa-12345678";
const EXECUTA_METHOD = "embed";

const MODE_HINTS = {
  direct:
    "Calls the host embeddings provider directly from the iframe (anna.llm.embed).",
  executa:
    "Invokes the Executa, which then asks the host to compute the embedding (embeddings/create reverse-RPC).",
};

const $ = (id) => document.getElementById(id);
const errBox = $("errors");
const modeSel = $("embed-mode");
const modeHint = $("mode-hint");

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

function resetResult() {
  $("r-model").textContent = "—";
  $("r-dims").textContent = "—";
  $("r-tokens").textContent = "—";
  $("r-latency").textContent = "—";
  $("r-cost").textContent = "—";
  $("r-preview").textContent = "(loading…)";
  $("r-raw").textContent = "(loading…)";
}

// Render either the canonical OpenAI-shaped response (direct path) or
// the Executa-wrapped {success, tool, data:{first_vector_preview, ...}}
// shape (executa path).
function renderResult(mode, reply) {
  $("r-raw").textContent = JSON.stringify(reply, null, 2);

  let model = "—",
    dims = "—",
    tokens = "—",
    latency = "—",
    cost = "—",
    preview = "(no vector)";

  if (mode === "direct") {
    // reply shape: { object, model, data:[{embedding, index}], usage, _meta }
    model = reply.model ?? "—";
    const vec = reply.data?.[0]?.embedding ?? [];
    dims = reply._meta?.dimensions ?? vec.length;
    tokens = reply.usage?.prompt_tokens ?? "—";
    latency = reply._meta?.latencyMs != null ? `${reply._meta.latencyMs} ms` : "—";
    cost = reply._meta?.costUsd != null ? `$${reply._meta.costUsd.toFixed(8)}` : "—";
    preview = JSON.stringify(vec.slice(0, 8), null, 2);
  } else {
    // Executa path: matrix host unwraps the plugin's {success,tool,data}
    // envelope before forwarding to the iframe, so `reply` IS the bare
    // tool payload: { count, dimensions, first_vector_preview, model,
    //   usage, _meta }. Fall back to reply.data for forward-compat in
    // case a future host version stops unwrapping.
    const data = reply?.first_vector_preview != null ? reply : (reply?.data ?? reply ?? {});
    model = data.model ?? "—";
    dims = data.dimensions ?? "—";
    tokens = data.usage?.prompt_tokens ?? "—";
    latency = data._meta?.latencyMs != null ? `${data._meta.latencyMs} ms` : "—";
    cost = data._meta?.costUsd != null ? `$${data._meta.costUsd.toFixed(8)}` : "—";
    preview = JSON.stringify(data.first_vector_preview ?? [], null, 2);
  }

  $("r-model").textContent = String(model);
  $("r-dims").textContent = String(dims);
  $("r-tokens").textContent = String(tokens);
  $("r-latency").textContent = String(latency);
  $("r-cost").textContent = String(cost);
  $("r-preview").textContent = preview;
}

modeSel?.addEventListener("change", () => {
  modeHint.textContent = MODE_HINTS[modeSel.value] || "";
});

async function runDirect(text, model) {
  const anna = await annaReady;
  return anna.llm.embed({
    input: [text],
    model: model || undefined,
  });
}

async function runViaExecuta(text, model) {
  const anna = await annaReady;
  return anna.tools.invoke({
    tool_id: EXECUTA_TOOL_ID,
    method: EXECUTA_METHOD,
    args: { texts: [text], model: model || "anna-managed-v1" },
  });
}

$("embed-btn").addEventListener("click", async () => {
  clearError();
  resetResult();
  const mode = modeSel?.value || "direct";
  const text = $("embed-input").value || "hello";
  const model = $("embed-model").value.trim();
  try {
    const reply =
      mode === "executa"
        ? await runViaExecuta(text, model)
        : await runDirect(text, model);
    renderResult(mode, reply);
  } catch (err) {
    $("r-preview").textContent = "(failed)";
    $("r-raw").textContent = "(failed)";
    showError(mode === "executa" ? "tools.invoke" : "llm.embed", err);
  }
});
