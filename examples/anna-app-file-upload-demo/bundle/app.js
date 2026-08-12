// Host Upload Demo — drives the host/uploadFile reverse-RPC (inline /
// negotiate / confirm) through a bundled Executa.
//
//   iframe ── tools.invoke(make_sample) ──▶ Executa ── writes scratch file ─▶ local disk
//   iframe ── tools.invoke(host_upload_path) ──▶ Executa ── host/uploadFile ─▶ host ─▶ R2
//                                                    │ inline (≤8 MiB) OR
//                                                    │ negotiate → PUT (plugin→R2) → confirm
//                                                    ▼
//                                              short-lived download URL (~30 min TTL)
//
// The crucial design point: the iframe never ships file bytes. It only sends
// small control messages (a size, or a local path). The Executa sources the
// bytes locally and — in negotiate mode — streams them straight to R2, so the
// payload never crosses the JSON-RPC stdio channel. That is what lets
// host/uploadFile scale past the stdio line limit.
//
// Loaded as a native ES module, so it imports the Anna App Runtime SDK below.
// The SDK (@anna-ai/app-runtime >= 0.5.0) is a named ESM export.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Bundled-executa handle → concrete tool_id resolution.
//
// The manifest references `bundled:file-upload-via-executa` (a stable handle).
// At publish time the server mints a real tool_id and writes it to
// `bundle/anna-tool-ids.js`. `anna-app dev` does the same with the local dev
// tool_id. We read the resolved id from the sidecar and only fall back to the
// hard-coded dev id (which must match executas/.../executa.json "tool_id")
// when the sidecar is absent.
const DEV_FALLBACK_TOOL_ID = "tool-test-file-upload-12345678";
const EXECUTA_TOOL_ID =
  (typeof window !== "undefined"
    && window.__ANNA_TOOL_IDS__
    && window.__ANNA_TOOL_IDS__["file-upload-via-executa"])
  || DEV_FALLBACK_TOOL_ID;

const MIB = 1024 * 1024;

// The host validates the upload MIME against a hard denylist only
// (executables, image/svg+xml) — the per-grant allowedMimeTypes whitelist
// was removed (forum #218): any non-blocked type is accepted. We still label
// synthetic samples with their real type so downstream readers (and the
// browser's Content-Disposition) behave sensibly — the host checks the MIME
// string, not the bytes.
const SAMPLE_MIME = "application/pdf";

// Extension → MIME for the upload-by-path field (declare the real type;
// non-media types are served with Content-Disposition: attachment).
const MIME_BY_EXT = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/markdown",
  json: "application/json",
};

function guessMime(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return MIME_BY_EXT[ext] || "application/octet-stream";
}

// Preset payload sizes chosen to straddle the two thresholds:
//   - 8 MiB  — the host/uploadFile inline cap (inline → negotiate)
//   - per-file upload_grant quota (dev = 25 MiB) → success vs UPLOAD_TOO_LARGE
const PRESETS = [
  { label: "1 MiB", bytes: 1 * MIB, hint: "inline" },
  { label: "6 MiB", bytes: 6 * MIB, hint: "inline" },
  { label: "12 MiB", bytes: 12 * MIB, hint: "negotiate" },
  { label: "18 MiB", bytes: 18 * MIB, hint: "negotiate" },
  { label: "30 MiB", bytes: 30 * MIB, hint: "quota ✗" },
];

const $ = (id) => document.getElementById(id);
const statusBox = $("status");
const rawBox = $("raw");

const annaReady = (async () => {
  const anna = await AnnaAppRuntime.connect();
  window.anna = anna;
  return anna;
})().catch((err) => {
  showStatus("runtime.connect", err, true);
  throw err;
});

function showStatus(label, payload, isError) {
  const msg = isError
    ? `${(payload && (payload.code || payload.error?.code)) || "error"}: ${
        (payload && (payload.message || payload.error?.message)) || String(payload)
      }`
    : payload;
  statusBox.textContent = `[${label}] ${msg}`;
  statusBox.classList.toggle("err", !!isError);
  statusBox.classList.toggle("ok", !isError);
}

// matrix host unwraps the plugin's {success, tool, data} envelope before
// forwarding to the iframe, so `reply` IS the bare tool payload. Fall back to
// reply.data for forward-compat in case a host stops unwrapping.
function unwrap(reply) {
  if (reply && typeof reply === "object" && reply.data && reply.tool) {
    return reply.data;
  }
  return reply ?? {};
}

async function invoke(method, args) {
  const anna = await annaReady;
  return anna.tools.invoke({ tool_id: EXECUTA_TOOL_ID, method, args });
}

function formatSize(n) {
  if (n == null) return "?";
  if (n < 1024) return `${n} B`;
  if (n < MIB) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / MIB).toFixed(2)} MiB`;
}

// ─── host/uploadFile orchestration ──────────────────────────────────────────

// Ask the Executa to persist a local file via host/uploadFile.
// Returns the unwrapped tool payload: { ok, mode, filename, size_bytes,
// mime_type, download_url, r2_key, expires_at }.
async function uploadPath(path, { filename, mime_type, purpose } = {}) {
  const reply = await invoke("host_upload_path", {
    path,
    filename: filename || "",
    mime_type: mime_type || "application/octet-stream",
    purpose: purpose || "user_artifact",
  });
  rawBox.textContent = JSON.stringify(reply, null, 2);
  return unwrap(reply);
}

// Generate a scratch payload of `bytes` on the Executa host, then upload it.
async function runPreset(preset) {
  const filename = `sample-${preset.label.replace(/\s+/g, "")}.pdf`;
  const entry = addResult(`${preset.label} sample`, "uploading", "make_sample…");
  try {
    const sampleReply = await invoke("make_sample", {
      size_bytes: preset.bytes,
      filename,
    });
    const sample = unwrap(sampleReply); // { ok, path, filename, size_bytes }
    if (!sample.path) throw new Error("make_sample returned no path");

    entry.detail = "host/uploadFile…";
    renderResults();

    const res = await uploadPath(sample.path, {
      filename: sample.filename,
      mime_type: SAMPLE_MIME,
      purpose: "user_artifact",
    });
    // A "success" without a usable download URL is a contract violation —
    // surface it as an error instead of a green ✓ (forum #168).
    const url = res.download_url || res.url || null;
    if (!url) {
      throw new Error(
        `host_upload_path returned no download_url (keys: ${Object.keys(res).join(", ")})`,
      );
    }
    entry.status = "done";
    entry.url = url;
    entry.expires_at = res.expires_at || null;
    entry.detail = `✓ ${formatSize(res.size_bytes ?? res.bytes)} · ${res.mode || "?"}`;
    showStatus("host_upload_path", `${preset.label} → ${res.mode} ✓`, false);
  } catch (err) {
    entry.status = "error";
    entry.detail = `✗ ${(err && (err.code || err.message)) || err}`;
    showStatus("host_upload_path", err, true);
  }
  renderResults();
}

// ─── Result list rendering ───────────────────────────────────────────────────

/** @type {{ title: string, status: string, detail: string, url?: string|null, expires_at?: string|null }[]} */
let results = [];

function addResult(title, status, detail) {
  const entry = { title, status, detail };
  results.unshift(entry); // newest first
  renderResults();
  return entry;
}

function renderResults() {
  const list = $("result-list");
  list.innerHTML = "";
  for (const entry of results) {
    const li = document.createElement("li");
    li.className = `file-item ${entry.status}`;

    const meta = document.createElement("div");
    meta.className = "fi-meta";
    const name = document.createElement("span");
    name.className = "fi-name";
    name.textContent = entry.title;
    meta.appendChild(name);

    const state = document.createElement("span");
    state.className = "fi-state";
    state.textContent = entry.detail || entry.status;

    li.appendChild(meta);
    li.appendChild(state);
    // Host Upload objects are transient and never listed, so the returned
    // short-lived link is the only deliverable — surface it inline.
    if (entry.url) {
      const a = document.createElement("a");
      a.className = "fi-link";
      a.href = entry.url;
      a.textContent = "open ↗";
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      if (entry.expires_at) a.title = `expires ${entry.expires_at}`;
      li.appendChild(a);
    }
    list.appendChild(li);
  }
}

// ─── Wiring: preset buttons ──────────────────────────────────────────────────

const presetRow = $("preset-row");
for (const preset of PRESETS) {
  const btn = document.createElement("button");
  btn.className = "secondary";
  btn.innerHTML = `${preset.label}<span class="muted preset-hint"> · ${preset.hint}</span>`;
  btn.addEventListener("click", async () => {
    presetRow.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await runPreset(preset);
    } finally {
      presetRow.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  });
  presetRow.appendChild(btn);
}

// ─── Wiring: upload by local path ────────────────────────────────────────────

$("path-upload-btn").addEventListener("click", async () => {
  const path = $("path-input").value.trim();
  if (!path) {
    showStatus("host_upload_path", "enter an absolute local path", true);
    return;
  }
  const purpose = $("purpose-select").value;
  const filename = path.split("/").pop() || path;
  const entry = addResult(filename, "uploading", "host/uploadFile…");
  $("path-upload-btn").disabled = true;
  try {
    const res = await uploadPath(path, {
      filename,
      mime_type: guessMime(filename),
      purpose,
    });
    entry.status = "done";
    entry.url = res.download_url || null;
    entry.expires_at = res.expires_at || null;
    entry.detail = `✓ ${formatSize(res.size_bytes)} · ${res.mode || "?"}`;
    showStatus("host_upload_path", `${filename} → ${res.mode} ✓`, false);
  } catch (err) {
    entry.status = "error";
    entry.detail = `✗ ${(err && (err.code || err.message)) || err}`;
    showStatus("host_upload_path", err, true);
  } finally {
    $("path-upload-btn").disabled = false;
    renderResults();
  }
});

renderResults();
