/**
 * Media Studio — anna-app bundle.
 *
 * Reference App for the platform media generation surface
 * (`platform-media-generation-video-audio.md` §12), built on
 * `@anna-ai/app-runtime` >= 0.20.0:
 *
 *   anna.llm.catalog({serviceType})        → capability + CU pricing discovery
 *   anna.video.generate(...)               → async job submit ({jobId, estimatedCostCU})
 *   anna.video.generateAwait(args, opts)   → submit + media_job events + poll
 *   anna.video.{getJob,cancelJob,listJobs} → job lifecycle (getJob re-signs URLs)
 *   anna.audio.speak(...)                  → sync TTS narration
 *   anna.image.generate(...)               → first frame for image-to-video
 *   anna.upload.inline / negotiate+confirm → persist artifacts to host storage
 *
 * Demo walkthrough implemented here (§12.3):
 *   1. text-to-video with estimatedCostCU + live progress;
 *   2. generateAwait with AbortController cancel (queued cancel = refund);
 *   3. image.generate → image-to-video chaining;
 *   4. audio.speak narration into an <audio> player;
 *   5. persist to host storage + chat.append_artifact + URL-expiry recovery
 *      (getJob re-sign button);
 *   6. error demo area (invalid params / quota probes) showing canonical
 *      error codes.
 *
 * Errors arrive as standard RPC errors with `.code` set to the facade's
 * canonical name; the original numeric JSON-RPC code (-32120…-32147 for the
 * media segment) is preserved in `.details.jsonrpc_code`.
 */

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const STORAGE_KEY_HISTORY = "media-studio:history";
const MAX_HISTORY = 8;

const state = {
  catalog: [], // video_gen models from anna.llm.catalog
  ttsCatalog: [], // tts models from anna.llm.catalog
  remainingCU: null,
  currentJob: null, // last submitted/loaded job view
  abort: null, // AbortController for the in-flight generateAwait
  history: [], // [{jobId, prompt, model, state}]
  narration: null, // last audio.speak result + request echo
};

let anna = null;

// ─── boot ───────────────────────────────────────────────────────────

async function init() {
  bindUi();
  try {
    anna = await AnnaAppRuntime.connect();
  } catch (e) {
    setStatus(`Failed to connect to host: ${e.message}`, "err");
    return;
  }
  try {
    anna.window.setTitle("Media Studio");
  } catch (_) {}

  await Promise.all([loadCatalog(), loadTtsCatalog(), restoreHistory()]);
  setStatus("Connected. Pick a model and generate.", "ok");
}

// ─── capability discovery (§7.6) ────────────────────────────────────

async function loadCatalog() {
  try {
    const res = await anna.llm.catalog({ serviceType: "video_gen" });
    state.catalog = res.models || [];
    state.remainingCU = res.quota ? res.quota.remainingCU : null;
  } catch (e) {
    setStatus(`llm.catalog failed: ${describeError(e)}`, "warn");
    state.catalog = [];
  }
  fillModelSelect(el("model"), state.catalog, "Default (admin/catalog pick)");
  syncParamSpace();
  updateEstimate();
  updateTtsEstimate(); // shares the quota suffix
}

/** Same discovery surface as video, different serviceType. TTS entries
 *  carry `capabilities.{voices,defaultVoice,options,maxCharsPerCall,
 *  timestamps}` + `pricing.cuPer1kChars`. */
async function loadTtsCatalog() {
  try {
    const res = await anna.llm.catalog({ serviceType: "tts" });
    state.ttsCatalog = res.models || [];
    if (res.quota) state.remainingCU = res.quota.remainingCU;
  } catch (e) {
    setStatus(`llm.catalog(tts) failed: ${describeError(e)}`, "warn");
    state.ttsCatalog = [];
  }
  fillModelSelect(el("tts-model"), state.ttsCatalog, "Default (admin/catalog pick)");
  syncTtsParamSpace();
  updateTtsEstimate();
}

/** Quota-only refresh after a billed call — leaves every select untouched
 *  (a full catalog reload would reset the user's picks). */
async function refreshQuota() {
  try {
    const res = await anna.llm.catalog({ serviceType: "video_gen" });
    if (res.quota) state.remainingCU = res.quota.remainingCU;
  } catch (_) {}
  updateEstimate();
  updateTtsEstimate();
}

function fillModelSelect(sel, models, placeholder) {
  sel.innerHTML = `<option value="">${placeholder}</option>`;
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m.modelName;
    opt.textContent =
      (m.displayName || m.modelName) + (m.isUserPreferred ? " ★" : "");
    sel.appendChild(opt);
  }
}

function selectedModel() {
  const name = el("model").value;
  return state.catalog.find((m) => m.modelName === name) || null;
}

/** Repopulate duration / resolution / ratio / audio from the model's
 *  advertised capability space; empty selects mean "provider default". */
function syncParamSpace() {
  const m = selectedModel();
  const caps = (m && m.capabilities) || {};

  const dur = el("duration");
  dur.innerHTML = '<option value="">default</option>';
  // Server shape: {min,max,default} (range) or {values,default} (enum).
  const d = caps.duration || {};
  if (Array.isArray(d.values)) {
    for (const v of d.values) addOpt(dur, v, `${v}s`);
  } else if (d.min != null && d.max != null) {
    for (let v = d.min; v <= d.max; v += d.step || 1) addOpt(dur, v, `${v}s`);
  }
  // "default" labels show the server-side default so the user sees what
  // an omitted parameter will actually resolve to.
  if (d.default != null) dur.options[0].textContent = `default (${d.default}s)`;

  fillSelect(el("resolution"), caps.resolutions);
  if (caps.defaultResolution)
    el("resolution").options[0].textContent = `default (${caps.defaultResolution})`;
  fillSelect(el("ratio"), caps.aspectRatios);

  const audio = el("audio-toggle");
  audio.innerHTML = "";
  if (caps.audio) {
    const def = caps.defaultGenerateAudio !== false;
    addOpt(audio, "", `default (${def ? "on" : "off"})`);
    addOpt(audio, "true", "on");
    addOpt(audio, "false", "off");
  } else {
    addOpt(audio, "", "n/a");
  }

  el("i2v-btn").disabled = m ? !caps.imageToVideo : false;
}

function fillSelect(sel, values) {
  sel.innerHTML = '<option value="">default</option>';
  for (const v of values || []) addOpt(sel, v, v);
}

function addOpt(sel, value, label) {
  const opt = document.createElement("option");
  opt.value = String(value);
  opt.textContent = label;
  sel.appendChild(opt);
}

/** Client-side CU estimate mirroring the catalog `pricing` block —
 *  authoritative number still comes back as `estimatedCostCU`. */
function updateEstimate() {
  const m = selectedModel();
  const box = el("estimate");
  if (!m || !m.pricing) {
    box.textContent = quotaSuffix("estimate: server-side");
    return;
  }
  const p = m.pricing;
  const caps = m.capabilities || {};
  // Resolve omitted params exactly like the server does, so the estimate
  // tracks the authoritative estimatedCostCU.
  const dur =
    Number(el("duration").value) ||
    (caps.duration && caps.duration.default) ||
    p.minBilledSeconds ||
    5;
  const res = el("resolution").value || caps.defaultResolution || "";
  const audioSel = el("audio-toggle").value;
  const audioOn =
    audioSel === "" ? caps.defaultGenerateAudio !== false && caps.audio : audioSel === "true";
  let perSec = null;
  if (audioOn && p.cuPerSecondAudioOn != null) perSec = p.cuPerSecondAudioOn;
  else if (p.cuByResolution && res && p.cuByResolution[res] != null)
    perSec = p.cuByResolution[res];
  else if (p.cuByResolution) perSec = Object.values(p.cuByResolution)[0];
  else if (p.cuPerSecond != null) perSec = p.cuPerSecond;
  box.textContent = quotaSuffix(
    perSec != null
      ? `estimate: ~${perSec * Math.max(dur, p.minBilledSeconds || 0)} CU`
      : "estimate: server-side"
  );
}

function quotaSuffix(text) {
  return state.remainingCU != null
    ? `${text} · quota ${state.remainingCU} CU`
    : text;
}

// ─── video generation (steps 1–3) ───────────────────────────────────

function collectArgs(extra = {}) {
  const args = { prompt: el("prompt").value.trim(), ...extra };
  if (!args.prompt && !args.imageUrl) {
    throw Object.assign(new Error("Enter a prompt first."), { code: "ui" });
  }
  const model = el("model").value;
  if (model) args.model = model;
  if (el("duration").value) args.durationSec = Number(el("duration").value);
  if (el("resolution").value) args.resolution = el("resolution").value;
  if (el("ratio").value) args.aspectRatio = el("ratio").value;
  if (el("audio-toggle").value) args.generateAudio = el("audio-toggle").value === "true";
  args.clientTag = `media-studio-${Date.now()}`;
  return args;
}

async function onGenerate(extraArgs = {}) {
  let args;
  try {
    args = collectArgs(extraArgs);
  } catch (e) {
    setStatus(e.message, "warn");
    return;
  }
  state.abort = new AbortController();
  setBusy(true);
  showProgress({ state: "queued" });
  setStatus("Submitting job…");
  try {
    const view = await anna.video.generateAwait(args, {
      signal: state.abort.signal,
      onProgress: (p) => {
        showProgress(p);
        setStatus(
          p.state === "running" || p.phase === "IN_PROGRESS"
            ? "Rendering…"
            : "Queued at provider…"
        );
      },
    });
    state.currentJob = view;
    renderJob(view);
    setStatus(`Done — billed ${view.billedCostCU ?? "?"} CU.`, "ok");
    // History persistence is cosmetic — never let it mask a successful job.
    pushHistory(view, args.prompt).catch(() => {});
  } catch (e) {
    if (e.code === "cancelled") {
      setStatus("Job cancelled — queued cancels are fully refunded.", "warn");
    } else {
      setStatus(`Generation failed: ${describeError(e)}`, "err");
    }
    if (e.jobId) {
      state.currentJob = { jobId: e.jobId, state: e.state || "failed" };
      el("refresh-btn").disabled = false;
    }
    hideProgress();
  } finally {
    state.abort = null;
    setBusy(false);
    refreshQuota();
  }
}

function onCancel() {
  if (state.abort) {
    state.abort.abort(); // → video.cancelJob under the hood
    setStatus("Cancel requested…", "warn");
  }
}

/** Step 3: image.generate → first frame → image-to-video. */
async function onImageToVideo() {
  const prompt = el("prompt").value.trim();
  if (!prompt) {
    setStatus("Enter a prompt first.", "warn");
    return;
  }
  setBusy(true);
  setStatus("Generating first frame via image.generate…");
  try {
    const res = await anna.image.generate({
      prompt: `Single cinematic keyframe, first frame of a video: ${prompt}`,
      n: 1,
    });
    const img = (res.images || [])[0];
    if (!img || !img.url) throw new Error("image.generate returned no image");
    setStatus("First frame ready — animating it…");
    setBusy(false);
    await onGenerate({ imageUrl: img.url });
  } catch (e) {
    setStatus(`Image → video failed: ${describeError(e)}`, "err");
    setBusy(false);
  }
}

// ─── rendering / progress ───────────────────────────────────────────

/** Progress payloads carry {seq, state, ...progress}. fal-backed models
 *  report a phase (IN_QUEUE → queuePosition / IN_PROGRESS) rather than a
 *  percentage, so the bar is staged: queued ≈ 10 %, running = indeterminate
 *  pulse, and `percent` is honoured whenever a provider supplies one. */
function showProgress(p) {
  el("progress-row").hidden = false;
  const fill = el("progress-fill");
  const pct = typeof p.percent === "number" ? p.percent : null;
  const running = p.state === "running" || p.phase === "IN_PROGRESS";
  if (pct != null) {
    fill.style.width = `${pct}%`;
    fill.classList.remove("indeterminate");
  } else if (running) {
    fill.style.width = "100%";
    fill.classList.add("indeterminate");
  } else {
    fill.style.width = "10%";
    fill.classList.remove("indeterminate");
  }
  let label = running ? "rendering" : p.state || "queued";
  if (pct != null) label += ` ${pct}%`;
  else if (!running && p.queuePosition != null)
    label += ` — position ${p.queuePosition} in queue`;
  if (p.message) label += ` — ${p.message}`;
  el("progress-text").textContent = label;
}

function hideProgress() {
  el("progress-row").hidden = true;
}

function renderJob(view) {
  hideProgress();
  const result = view.result || {};
  const preview = el("preview");
  preview.classList.remove("preview--empty");
  preview.innerHTML = "";
  if (result.url) {
    const video = document.createElement("video");
    video.src = result.url;
    video.controls = true;
    video.playsInline = true;
    preview.appendChild(video);
  } else {
    preview.innerHTML = `<span class="preview__hint">Job ${view.jobId} is ${view.state}.</span>`;
  }
  el("meta-model").textContent = `model: ${view.model || "—"}`;
  el("meta-cost").textContent = `cost: ${
    view.billedCostCU ?? view.estimatedCostCU ?? "—"
  } CU`;
  el("meta-job").textContent = `job: ${view.jobId}`;
  el("meta-r2").textContent = result.expiresIn
    ? `url expires in ${result.expiresIn}s`
    : "r2: —";
  el("persist-btn").disabled = !result.url;
  el("chat-btn").disabled = !result.url;
  el("refresh-btn").disabled = !view.jobId;
}

// ─── narration (step 4) — catalog-driven, mirrors the video panel ──

function selectedTtsModel() {
  const name = el("tts-model").value;
  return state.ttsCatalog.find((m) => m.modelName === name) || null;
}

/** Voices / language / format / speed / timestamps all come from the
 *  selected model's advertised capability space. "Default" picks mean
 *  the host applies the model's own defaults. Voice ids are a per-model
 *  whitelist — a voice from another model fails with -32145. */
function syncTtsParamSpace() {
  const m = selectedTtsModel();
  const caps = (m && m.capabilities) || {};
  const opts = caps.options || {};

  const voice = el("tts-voice");
  voice.innerHTML = "";
  const voices = caps.voices || [];
  if (voices.length) {
    for (const v of voices) {
      const label =
        (v.label && (v.label.en || v.label.zh)) || v.id;
      const langs = Array.isArray(v.languages) ? ` (${v.languages.join("/")})` : "";
      addOpt(voice, v.id, label + langs);
    }
    voice.value = caps.defaultVoice && voices.some((v) => v.id === caps.defaultVoice)
      ? caps.defaultVoice
      : voices[0].id;
  } else {
    addOpt(voice, "", m ? "model default" : "default");
  }

  const lang = el("tts-language");
  lang.innerHTML = '<option value="">default</option>';
  for (const v of (opts.language && opts.language.values) || []) addOpt(lang, v, v);
  lang.disabled = !(opts.language && opts.language.values && opts.language.values.length);

  const fmt = el("tts-format");
  fmt.innerHTML = "";
  const fmts = (opts.format && opts.format.values) || ["mp3"];
  for (const v of fmts) addOpt(fmt, v, v);
  fmt.value = (opts.format && opts.format.default) || fmts[0];

  const speed = el("tts-speed");
  const sp = opts.speed;
  speed.disabled = !sp;
  speed.min = sp ? sp.min : 0.5;
  speed.max = sp ? sp.max : 2;
  speed.value = sp ? sp.default ?? 1 : 1;
  el("tts-speed-val").textContent = sp ? `${Number(speed.value).toFixed(1)}×` : "n/a";

  el("tts-timestamps-row").hidden = !caps.timestamps;
  el("tts-timestamps").checked = false;

  const ta = el("narration");
  ta.maxLength = caps.maxCharsPerCall || 10000;
}

/** Live character count + CU estimate from `pricing.cuPer1kChars`
 *  (host bills on the authoritative `charCount`, minimum 1 CU). */
function updateTtsEstimate() {
  const m = selectedTtsModel();
  const caps = (m && m.capabilities) || {};
  const n = el("narration").value.trim().length;
  const cap = caps.maxCharsPerCall ? ` / ${caps.maxCharsPerCall}` : "";
  let est = "estimate: server-side";
  if (m && m.pricing && m.pricing.cuPer1kChars != null && n > 0) {
    est = `estimate: ~${Math.max(1, Math.ceil((n / 1000) * m.pricing.cuPer1kChars))} CU`;
  }
  const over = caps.maxCharsPerCall && n > caps.maxCharsPerCall;
  el("tts-estimate").textContent = quotaSuffix(
    `${n}${cap} chars · ${est}${over ? " · exceeds model cap" : ""}`
  );
  el("tts-estimate").classList.toggle("estimate--warn", Boolean(over));
}

function collectTtsArgs() {
  const text = el("narration").value.trim();
  if (!text) {
    throw Object.assign(new Error("Enter narration text first."), { code: "ui" });
  }
  const args = { text, delivery: "url" };
  if (el("tts-model").value) args.model = el("tts-model").value;
  if (el("tts-voice").value) args.voice = el("tts-voice").value;
  if (el("tts-language").value) args.language = el("tts-language").value;
  if (el("tts-format").value) args.format = el("tts-format").value;
  if (!el("tts-speed").disabled) {
    const sp = Number(el("tts-speed").value);
    if (sp !== 1) args.speed = sp;
  }
  if (!el("tts-timestamps-row").hidden && el("tts-timestamps").checked)
    args.timestamps = true;
  return args;
}

async function onSpeak(extraArgs = {}) {
  let args;
  try {
    args = { ...collectTtsArgs(), ...extraArgs };
  } catch (e) {
    setStatus(e.message, "warn");
    return;
  }
  el("speak-btn").disabled = true;
  setStatus("Synthesizing narration…");
  try {
    const res = await anna.audio.speak(args);
    state.narration = { ...res, request: args };
    renderNarration(res, args);
    setStatus(
      `Narration ready — ${res.charCount} chars, ${res.billedCostCU} CU.`,
      "ok"
    );
  } catch (e) {
    setStatus(`audio.speak failed: ${describeError(e)}`, "err");
  } finally {
    el("speak-btn").disabled = false;
    refreshQuota();
  }
}

function renderNarration(res, args) {
  const player = el("narration-player");
  if (res.url) {
    player.src = res.url;
  } else if (res.audioBase64) {
    player.src = `data:${res.mimeType || "audio/mpeg"};base64,${res.audioBase64}`;
  }
  el("narration-box").hidden = false;
  player.play().catch(() => {});
  el("tts-meta-model").textContent = `model: ${res.model || args.model || "default"}`;
  el("tts-meta-voice").textContent = `voice: ${res.voice || args.voice || "default"}`;
  el("tts-meta-chars").textContent = `chars: ${res.charCount ?? "—"}`;
  el("tts-meta-cost").textContent =
    `cost: ${res.billedCostCU ?? "—"} CU` +
    (res.timestamps ? ` · ${res.timestamps.length} word timestamps` : "");
  el("persist-audio-btn").disabled = !(res.url || res.audioBase64);
}

// ─── persistence + URL recovery (step 5) ────────────────────────────

const INLINE_LIMIT = 8 * 1024 * 1024;

/** Upload a Blob to host storage: ≤ 8 MB inline, otherwise negotiate a
 *  direct-to-R2 PUT and confirm. Shared by video + narration persist. */
async function persistBlob(blob, { filename, mime, metadata }) {
  if (blob.size <= INLINE_LIMIT) {
    return anna.upload.inline({
      filename,
      mime_type: mime,
      content_b64: await blobToBase64(blob),
      purpose: "user_artifact",
      metadata: { source: "media-studio", ...(metadata || {}) },
    });
  }
  const nego = await anna.upload.negotiate({
    filename,
    mime_type: mime,
    size_bytes: blob.size,
    purpose: "user_artifact",
  });
  const put = await fetch(nego.put_url, {
    method: "PUT",
    headers: { "Content-Type": mime, ...(nego.headers || {}) },
    body: blob,
  });
  if (!put.ok) throw new Error(`R2 PUT failed: ${put.status}`);
  return anna.upload.confirm({ r2_key: nego.r2_key });
}

async function onPersist() {
  const result = state.currentJob && state.currentJob.result;
  if (!result || !result.url) return;
  setStatus("Fetching video bytes for upload…");
  try {
    const resp = await fetch(result.url);
    if (!resp.ok) throw new Error(`fetch ${resp.status} — URL may have expired; use Re-sign`);
    const uploaded = await persistBlob(await resp.blob(), {
      filename: `media-studio-${state.currentJob.jobId}.mp4`,
      mime: result.mimeType || "video/mp4",
      metadata: { job_id: state.currentJob.jobId },
    });
    setStatus(`Persisted → ${uploaded.r2_key || uploaded.download_url}`, "ok");
  } catch (e) {
    setStatus(`Persist failed: ${describeError(e)}`, "err");
  }
}

async function onPersistAudio() {
  const n = state.narration;
  if (!n) return;
  setStatus("Fetching narration bytes for upload…");
  try {
    const mime = n.mimeType || "audio/mpeg";
    const src = n.url || `data:${mime};base64,${n.audioBase64}`;
    const resp = await fetch(src);
    if (!resp.ok) throw new Error(`fetch ${resp.status} — narration URL may have expired; speak again`);
    const ext = mime.includes("wav") ? "wav" : "mp3";
    const uploaded = await persistBlob(await resp.blob(), {
      filename: `media-studio-narration-${Date.now()}.${ext}`,
      mime,
      metadata: { tts_model: n.model || n.request.model || null },
    });
    setStatus(`Narration persisted → ${uploaded.r2_key || uploaded.download_url}`, "ok");
  } catch (e) {
    setStatus(`Persist failed: ${describeError(e)}`, "err");
  }
}

async function onSendToChat() {
  const result = state.currentJob && state.currentJob.result;
  if (!result || !result.url) return;
  try {
    await anna.chat.append_artifact({
      artifact: {
        kind: "video",
        app_slug: "media-studio",
        summary: `Generated video ${state.currentJob.jobId}`,
        data: {
          url: result.url,
          mime_type: result.mimeType || "video/mp4",
          job_id: state.currentJob.jobId,
        },
      },
    });
    setStatus("Sent to chat.", "ok");
  } catch (e) {
    setStatus(`chat.append_artifact failed: ${describeError(e)}`, "err");
  }
}

/** URL-expiry recovery: any later getJob on a succeeded job re-signs the
 *  result URL (§7.3) — no regeneration, no extra billing. */
async function onResign() {
  const jobId = state.currentJob && state.currentJob.jobId;
  if (!jobId) return;
  setStatus("Re-signing via video.getJob…");
  try {
    const view = await anna.video.getJob({ jobId });
    state.currentJob = view;
    renderJob(view);
    setStatus(
      view.result
        ? `Fresh URL issued (expires in ${view.result.expiresIn}s).`
        : `Job is ${view.state}.`,
      "ok"
    );
  } catch (e) {
    setStatus(`getJob failed: ${describeError(e)}`, "err");
  }
}

// ─── history via APS storage ────────────────────────────────────────

async function restoreHistory() {
  try {
    const raw = await anna.storage.get({ key: STORAGE_KEY_HISTORY });
    // storage.get → {value} ; value is null on first run
    let val = raw && typeof raw === "object" && "value" in raw ? raw.value : raw;
    if (typeof val === "string") val = JSON.parse(val);
    state.history = Array.isArray(val) ? val : [];
  } catch (_) {
    state.history = [];
  }
  renderHistory();
}

async function pushHistory(view, prompt) {
  if (!Array.isArray(state.history)) state.history = [];
  state.history.unshift({
    jobId: view.jobId,
    prompt: (prompt || "").slice(0, 120),
    model: view.model,
    state: view.state,
  });
  state.history = state.history.slice(0, MAX_HISTORY);
  renderHistory();
  try {
    await anna.storage.set({
      key: STORAGE_KEY_HISTORY,
      value: JSON.stringify(state.history),
    });
  } catch (_) {}
}

function renderHistory() {
  const box = el("history");
  box.innerHTML = "";
  for (const h of state.history) {
    const item = document.createElement("button");
    item.className = "history__item";
    item.textContent = `${h.state === "succeeded" ? "✓" : "·"} ${
      h.prompt || h.jobId
    }`;
    item.title = `${h.jobId} (${h.model || "?"})`;
    // Recover any past job purely from its id — getJob re-signs the URL.
    item.addEventListener("click", async () => {
      state.currentJob = { jobId: h.jobId };
      await onResign();
    });
    box.appendChild(item);
  }
}

// ─── error demos (step 6) ───────────────────────────────────────────

async function onErrInvalid() {
  setStatus("Submitting durationSec=9999 on purpose…");
  try {
    await anna.video.generate({
      prompt: "error demo",
      durationSec: 9999,
      clientTag: `media-studio-err-${Date.now()}`,
    });
    setStatus("Unexpectedly accepted — model has no duration bounds.", "warn");
  } catch (e) {
    setStatus(`Expected validation error → ${describeError(e)}`, "warn");
  }
}

/** -32145: voices are a per-model whitelist; the error carries the valid
 *  ids in `.details.allowed` so a UI can recover without a second catalog call. */
async function onErrVoice() {
  setStatus("Submitting voice='not-a-voice' on purpose…");
  const text = el("narration").value.trim() || "Error demo.";
  try {
    await anna.audio.speak({ text, voice: "not-a-voice", model: el("tts-model").value || undefined });
    setStatus("Unexpectedly accepted — model has no voice whitelist.", "warn");
  } catch (e) {
    const allowed = e.details && e.details.allowed;
    setStatus(
      `Expected voice error → ${describeError(e)}` +
        (Array.isArray(allowed) ? ` · allowed: ${allowed.slice(0, 6).join(", ")}…` : ""),
      "warn"
    );
  }
}

async function onQuotaProbe() {
  try {
    const res = await anna.llm.catalog({ serviceType: "video_gen" });
    const tts = await anna.llm.catalog({ serviceType: "tts" });
    state.remainingCU = res.quota ? res.quota.remainingCU : null;
    updateEstimate();
    updateTtsEstimate();
    setStatus(
      `Catalog: ${(res.models || []).length} video models, ` +
        `${(tts.models || []).length} TTS models, ` +
        `remaining quota ${state.remainingCU} CU. Jobs are rejected with ` +
        `quota_exhausted when the estimate exceeds this pool.`,
      "ok"
    );
  } catch (e) {
    setStatus(describeError(e), "err");
  }
}

// ─── helpers ────────────────────────────────────────────────────────

function describeError(e) {
  const code = e.code || "unknown";
  const num =
    e.details && e.details.jsonrpc_code ? ` (${e.details.jsonrpc_code})` : "";
  return `${code}${num}: ${e.message || e}`;
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1]);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

function el(id) {
  return document.getElementById(id);
}

function setStatus(text, tone) {
  const box = el("status");
  box.textContent = text;
  box.dataset.tone = tone || "";
}

function setBusy(busy) {
  el("generate-btn").disabled = busy;
  el("i2v-btn").disabled = busy;
  el("cancel-btn").disabled = !busy;
}

function bindUi() {
  el("generate-btn").addEventListener("click", () => onGenerate());
  el("cancel-btn").addEventListener("click", onCancel);
  el("i2v-btn").addEventListener("click", onImageToVideo);
  el("speak-btn").addEventListener("click", () => onSpeak());
  el("persist-btn").addEventListener("click", onPersist);
  el("chat-btn").addEventListener("click", onSendToChat);
  el("refresh-btn").addEventListener("click", onResign);
  el("err-invalid-btn").addEventListener("click", onErrInvalid);
  el("err-voice-btn").addEventListener("click", onErrVoice);
  el("err-quota-btn").addEventListener("click", onQuotaProbe);
  el("persist-audio-btn").addEventListener("click", onPersistAudio);
  el("tts-model").addEventListener("change", () => {
    syncTtsParamSpace();
    updateTtsEstimate();
  });
  el("narration").addEventListener("input", updateTtsEstimate);
  el("tts-speed").addEventListener("input", () => {
    el("tts-speed-val").textContent = `${Number(el("tts-speed").value).toFixed(1)}×`;
  });
  el("model").addEventListener("change", () => {
    syncParamSpace();
    updateEstimate();
  });
  for (const id of ["duration", "resolution", "audio-toggle"])
    el(id).addEventListener("change", updateEstimate);
}

init();
