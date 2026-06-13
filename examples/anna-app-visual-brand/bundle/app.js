/**
 * Visual Brand — anna-app bundle.
 *
 * Demonstrates the host-mediated image + upload services introduced by
 * `executa-llm-image-generation` RFC v2, accessed via the typed SDK
 * helpers shipped in `@anna-ai/app-runtime` >=0.4.0:
 *
 *   anna.image.generate(...)  → image.generate RPC
 *   anna.image.edit(...)      → image.edit RPC
 *   anna.upload.inline(...)   → upload.inline RPC (≤ 8 MB)
 *
 * Transport: all five calls are plain postMessage RPCs routed through
 * the host page to `/api/v1/anna-apps/runtime/rpc` — symmetric with
 * `anna.storage.*` and `anna.llm.complete`. The iframe never holds an
 * `app_session_token`; the dispatcher mints one server-side per window
 * and forwards to `app_llm_facade.{image_generate, image_edit,
 * upload_inline, …}`.
 *
 * Errors arrive as standard RPC errors with `.code` set to the facade's
 * canonical name (`APP_NOT_GRANTED`, `APP_QUOTA_EXCEEDED`,
 * `APP_INVALID_REQUEST`, `APP_PROVIDER_ERROR`, …) or `not_implemented`.
 * The original numeric JSON-RPC code is preserved in `.details.jsonrpc_code`.
 * We map those to
 * actionable hints; the user is told whether to adjust the per-app
 * image_grant / upload_grant in the Anna Admin panel.
 *
 * Storage: last prompt + last style are checkpointed into APS via
 * `anna.storage.set` so the canvas survives a window reload.
 */

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const STORAGE_KEY_PROMPT = "visual-brand:last-prompt";
const STORAGE_KEY_STYLE = "visual-brand:last-style";
const MAX_HISTORY = 6;

const state = {
  current: null, // {url, mimeType, model, prompt, style}
  history: [],   // rotating ring of past generations
};

let anna = null;

// ─── boot ───────────────────────────────────────────────────────────

async function init() {
  bindUi();
  try {
    anna = await AnnaAppRuntime.connect();
    setStatus("Connected. Type a prompt and hit Generate.", "ok");
  } catch (e) {
    setStatus("Standalone preview — Anna runtime unavailable.", "warn");
    console.warn("[visual-brand]", e);
    return;
  }

  // Restore last inputs.
  try {
    const p = await anna.storage.get({ key: STORAGE_KEY_PROMPT });
    if (p?.exists && typeof p.value === "string") {
      document.getElementById("prompt").value = p.value;
    }
  } catch { /* ignore */ }
  try {
    const s = await anna.storage.get({ key: STORAGE_KEY_STYLE });
    if (s?.exists && typeof s.value === "string") {
      const sel = document.getElementById("style");
      if ([...sel.options].some((o) => o.value === s.value)) sel.value = s.value;
    }
  } catch { /* ignore */ }
}

// ─── UI binding ─────────────────────────────────────────────────────

function bindUi() {
  document.getElementById("generate-btn").addEventListener("click", onGenerate);
  document.getElementById("restyle-btn").addEventListener("click", onRestyle);
  document.getElementById("upload-btn").addEventListener("click", onUpload);
}

// ─── actions ────────────────────────────────────────────────────────

async function onGenerate() {
  if (!anna) return setStatus("Not connected.", "err");
  const prompt = (document.getElementById("prompt").value || "").trim();
  const style = document.getElementById("style").value;
  const size = document.getElementById("size").value;
  if (!prompt) return setStatus("Prompt is empty.", "warn");

  setStatus("Generating…", "info");
  toggleButtons(true);
  try {
    const out = await anna.image.generate({
      prompt: `A ${style} poster: ${prompt}. High contrast, bold typography, single focal subject.`,
      n: 1,
      size,
      metadata: { source: "visual-brand", style },
    });
    setCurrent({ ...out.images?.[0], model: out.model, prompt, style });
    setQuota(out.quota_used);
    void anna.storage.set({ key: STORAGE_KEY_PROMPT, value: prompt });
    void anna.storage.set({ key: STORAGE_KEY_STYLE, value: style });
    setStatus("Generated. Edit or persist below.", "ok");
  } catch (e) {
    setStatus(formatError(e), "err");
  } finally {
    toggleButtons(false);
  }
}

async function onRestyle() {
  if (!anna || !state.current) return;
  const style = document.getElementById("style").value;
  setStatus("Restyling…", "info");
  toggleButtons(true);
  try {
    const out = await anna.image.edit({
      image_url: state.current.url,
      prompt: `Restyle this poster in a ${style} aesthetic. Preserve composition.`,
      n: 1,
      metadata: { source: "visual-brand", style, op: "restyle" },
    });
    setCurrent({ ...out.images?.[0], model: out.model, prompt: state.current.prompt, style });
    setStatus("Restyled.", "ok");
  } catch (e) {
    setStatus(formatError(e), "err");
  } finally {
    toggleButtons(false);
  }
}

async function onUpload() {
  if (!anna || !state.current) return;
  setStatus("Fetching image…", "info");
  toggleButtons(true);
  try {
    const resp = await fetch(state.current.url);
    if (!resp.ok) throw new Error(`fetch ${resp.status}`);
    const blob = await resp.blob();
    if (blob.size > 8 * 1024 * 1024) {
      throw new Error("image exceeds 8 MB inline cap; use negotiate+confirm flow");
    }
    const b64 = await blobToBase64(blob);
    setStatus("Uploading to host…", "info");
    const out = await anna.upload.inline({
      filename: `visual-brand-${Date.now()}.png`,
      mime_type: blob.type || "image/png",
      content_b64: b64,
      purpose: "user_artifact",
      metadata: { source: "visual-brand" },
    });
    setR2(out.r2_key);
    setStatus(`Persisted (${humanBytes(out.size_bytes ?? blob.size)}).`, "ok");
  } catch (e) {
    setStatus(formatError(e), "err");
  } finally {
    toggleButtons(false);
  }
}

// ─── view helpers ───────────────────────────────────────────────────

function setCurrent(img) {
  if (!img?.url) return;
  // Push previous into history.
  if (state.current) {
    state.history.unshift(state.current);
    state.history = state.history.slice(0, MAX_HISTORY);
  }
  state.current = img;
  const preview = document.getElementById("preview");
  preview.classList.remove("preview--empty");
  preview.innerHTML = "";
  const el = document.createElement("img");
  el.src = img.url;
  el.alt = img.prompt || "";
  preview.appendChild(el);
  document.getElementById("meta-model").textContent = `model: ${img.model || "—"}`;
  document.getElementById("restyle-btn").disabled = false;
  document.getElementById("upload-btn").disabled = false;
  renderHistory();
}

function renderHistory() {
  const wrap = document.getElementById("history");
  wrap.innerHTML = "";
  for (const h of state.history) {
    const div = document.createElement("button");
    div.className = "history__thumb";
    div.title = h.prompt || "";
    div.type = "button";
    const img = document.createElement("img");
    img.src = h.url;
    img.alt = "";
    div.appendChild(img);
    div.addEventListener("click", () => setCurrent(h));
    wrap.appendChild(div);
  }
}

function setQuota(q) {
  const el = document.getElementById("meta-quota");
  if (!q) return (el.textContent = "quota: —");
  const used = q.images_today ?? q.used ?? "?";
  const max = q.images_quota ?? q.limit ?? "?";
  el.textContent = `quota: ${used}/${max}`;
}

function setR2(key) {
  document.getElementById("meta-r2").textContent = key ? `r2: …${key.slice(-32)}` : "r2: —";
}

function setStatus(text, kind = "info") {
  const el = document.getElementById("status");
  el.textContent = text;
  el.dataset.kind = kind;
}

function toggleButtons(busy) {
  for (const id of ["generate-btn", "restyle-btn", "upload-btn"]) {
    document.getElementById(id).disabled = busy;
  }
  // Re-enable contextual buttons after busy=false.
  if (!busy && !state.current) {
    document.getElementById("restyle-btn").disabled = true;
    document.getElementById("upload-btn").disabled = true;
  }
}

function formatError(e) {
  const m = e?.message || String(e);
  const code = e?.code || "";
  // Map facade-level error codes (surfaced by the dispatcher) to
  // user-actionable hints. v0.4.0+ uses RPC error envelope shape
  // `{code, message, details}` — no more HTTP status branching.
  switch (code) {
    case "APP_NOT_GRANTED":
      return `Not granted — toggle the matching grant in Anna Admin. [${code}] (${m})`;
    case "APP_QUOTA_EXCEEDED":
      return `Quota exceeded — wait until your daily reset. [${code}] (${m})`;
    case "APP_INVALID_REQUEST":
      return `Invalid request — check inputs. [${code}] (${m})`;
    case "APP_PROVIDER_ERROR":
      return `Upstream provider error — retry shortly. [${code}] (${m})`;
    case "not_implemented":
      return `Host did not wire this service. [${code}] (${m})`;
    default:
      return code ? `${m} [${code}]` : m;
  }
}

async function blobToBase64(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.byteLength; i++) bin += String.fromCharCode(buf[i]);
  return btoa(bin);
}

function humanBytes(n) {
  if (typeof n !== "number") return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

init();
