/**
 * Platform Runtime Demo — UI logic.
 *
 * Calls the bundled `runtime-probe` Executa via `anna.tools.invoke`:
 *   probe_runtimes / check_requires / render_html
 * Outside Anna (design preview) the SDK import fails and the app shows a
 * static notice instead of crashing.
 */
import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Local-dev fallback; overridden at publish time by window.__ANNA_TOOL_IDS__.
const DEV_FALLBACK_TOOL_ID = "tool-test-runtime-probe-12345678";
const TOOL_ID =
  (typeof window !== "undefined"
    && window.__ANNA_TOOL_IDS__
    && window.__ANNA_TOOL_IDS__["runtime-probe"])
  || DEV_FALLBACK_TOOL_ID;

const $ = (id) => document.getElementById(id);

let anna = null;
try {
  anna = await AnnaAppRuntime.connect();
} catch {
  document.querySelector("header .sub").textContent =
    "Not running inside Anna — open this app from the Anna launcher.";
}

async function invoke(method, args = {}) {
  if (!anna) throw new Error("not connected to Anna");
  const result = await anna.tools.invoke({
    tool_id: TOOL_ID,
    method,
    args,
  });
  if (result && result.success === false) {
    const err = new Error(result.error || "tool call failed");
    err.data = result.data;
    throw err;
  }
  return (result && result.data) ?? result;
}

function renderKv(el, entries) {
  el.innerHTML = "";
  for (const [k, v] of entries) {
    const row = document.createElement("div");
    row.className = "kv-row";
    row.innerHTML = `<span class="k"></span><span class="v"></span>`;
    row.querySelector(".k").textContent = k;
    row.querySelector(".v").textContent = v;
    el.appendChild(row);
  }
}

// ── 1 · Probe ───────────────────────────────────────────────────────────
$("probe-btn").addEventListener("click", async () => {
  const badge = $("probe-badge");
  badge.textContent = "…";
  badge.className = "badge";
  try {
    const d = await invoke("probe_runtimes");
    const source = d.chrome ? d.chrome.source : "none";
    if (d.contract_present) {
      badge.textContent = "contract ✓";
      badge.classList.add("ok");
    } else if (source === "legacy-scan") {
      badge.textContent = "⚠ legacy-scan";
      badge.classList.add("warn");
    } else {
      badge.textContent = "✗ none";
      badge.classList.add("err");
    }
    renderKv($("probe-summary"), [
      ["profile", d.profile || "(not set — pre-contract Agent)"],
      ["agent", d.agent_version || "unknown"],
      [
        "chrome",
        d.chrome
          ? `${d.chrome.version || "?"} (${d.chrome.provider}, ${d.chrome.source})`
          : "not found",
      ],
      [
        "node",
        d.node.platform
          ? `${d.node.platform.version} platform`
          : `${d.node.running.version} own (dev mode — expected)`,
      ],
    ]);
    const advice = $("probe-advice");
    advice.innerHTML = "";
    for (const a of d.advice || []) {
      const li = document.createElement("li");
      li.textContent = a;
      advice.appendChild(li);
    }
    $("probe-env").textContent = JSON.stringify(d.env, null, 2);
  } catch (e) {
    badge.textContent = "error";
    badge.classList.add("err");
    renderKv($("probe-summary"), [["error", e.message]]);
  }
});

// ── 2 · Precheck ────────────────────────────────────────────────────────
$("precheck-btn").addEventListener("click", async () => {
  const out = $("precheck-result");
  const missingEl = $("precheck-missing");
  missingEl.hidden = true;
  try {
    const r = await invoke("check_requires", {
      node: $("req-node").value.trim(),
      chrome: $("req-chrome").value.trim(),
    });
    const rows = Object.entries(r.checks).map(([key, c]) => [
      `${key} ${c.ok ? "✓" : "✗"}`,
      `required ${c.required} · found ${c.found}${c.compare ? ` (${c.compare})` : ""}`,
    ]);
    rows.unshift(["install would", r.satisfied ? "succeed ✓" : "fail with runtime_missing ✗"]);
    renderKv(out, rows);
    if (!r.satisfied) {
      // Same shape as the Agent's install failure metadata.
      missingEl.textContent = JSON.stringify(
        { reason_code: "runtime_missing", missing_runtimes: r.missing_runtimes },
        null,
        2,
      );
      missingEl.hidden = false;
    }
  } catch (e) {
    renderKv(out, [["error", e.message]]);
  }
});

// ── 3 · Render ──────────────────────────────────────────────────────────
async function doRender(format) {
  const meta = $("render-meta");
  const img = $("render-img");
  img.hidden = true;
  renderKv(meta, [["status", `rendering ${format}…`]]);
  try {
    const r = await invoke("render_html", {
      format,
      width: Number($("render-w").value) || 1280,
      height: Number($("render-h").value) || 720,
      ...($("render-html").value.trim() ? { html: $("render-html").value } : {}),
    });
    renderKv(meta, [
      ["file", r.path],
      ["bytes", String(r.bytes)],
      ["elapsed", `${r.elapsed_ms} ms`],
      ["chrome", `${r.chrome.reported || r.chrome.version} (${r.chrome.source})`],
    ]);
    if (r.data_url) {
      img.src = r.data_url;
      img.hidden = false;
    }
  } catch (e) {
    const code = e.data && e.data.error_code ? ` [${e.data.error_code}]` : "";
    renderKv(meta, [["error", e.message + code]]);
    if (e.data && e.data.advice) {
      renderKv(meta, [["error", e.message + code], ["advice", e.data.advice.join(" — ")]]);
    }
  }
}
$("render-png").addEventListener("click", () => doRender("png"));
$("render-pdf").addEventListener("click", () => doRender("pdf"));
