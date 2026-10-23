/**
 * Media Studio — anna-app bundle.
 *
 * Reference App for the platform media generation surface
 * (`platform-media-generation-video-audio.md` §12 +
 * `platform-media-generation-music.md` §12.2), built on
 * `@anna-ai/app-runtime` >= 0.23.0:
 *
 *   anna.llm.catalog({serviceType})        → capability + CU pricing discovery
 *   anna.video.generate(...)               → async job submit ({jobId, estimatedCostCU})
 *   anna.video.generateAwait(args, opts)   → submit + media_job events + poll
 *   anna.video.{getJob,cancelJob,listJobs} → job lifecycle (getJob re-signs URLs)
 *   anna.music.generateAwait(args, opts)   → async music job (capability-driven form,
 *                                            idempotencyKey replay, -32165 alternatives)
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
 *      error codes;
 *   7. Music panel (music.md §12.2): catalog-driven lyrics / instrumental /
 *      duration / reference-image controls, idempotent replay, -32165
 *      MUSIC_FEATURE_UNSUPPORTED → alternatives chips, <audio> + lyrics echo,
 *      entry_payload prefill (agent → App hand-off), persist to storage.
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
  musicCatalog: [], // music_gen models from anna.llm.catalog
  musicJob: null, // last music job view
  musicAbort: null, // AbortController for the in-flight music generateAwait
  lastFirstFrameUrl: null, // image.generate output (reference image for Lyria)
  lastFirstFrameR2Key: null, // → anna.image.resign after the 30-min URL TTL
  imageAbort: null, // AbortController for the in-flight image.generateAwait (job mode)
  imageJob: null, // last image job view (ijob_…) when "Run first frame as job" is on
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

  await Promise.all([loadCatalog(), loadTtsCatalog(), loadMusicCatalog(), restoreHistory()]);
  applyEntryPayload(anna.entryPayload);
  // Agent → App hand-off: open_app_view(entry_payload={music:{prompt,lyrics,…}})
  // on a live window re-pushes the payload (single_instance) — prefill again.
  anna.on("entry_payload", (p) => applyEntryPayload(p));
  setStatus("Connected. Pick a model and generate.", "ok");
}

/** `entry_payload.music` prefills the Music panel (platform-operates-App
 *  hand-off, music.md §12.2 item 7). Nothing is auto-submitted. */
function applyEntryPayload(payload) {
  const m = payload && payload.music;
  if (!m || typeof m !== "object") return;
  if (m.prompt) el("music-prompt").value = String(m.prompt);
  if (m.lyrics) el("music-lyrics").value = String(m.lyrics);
  if (m.model && state.musicCatalog.some((x) => x.modelName === m.model)) el("music-model").value = m.model;
  if (typeof m.instrumental === "boolean") el("music-instrumental").checked = m.instrumental;
  if (m.idempotencyKey) el("music-idem").value = String(m.idempotencyKey);
  syncMusicParamSpace();
  if (m.durationSec) el("music-duration").value = String(m.durationSec);
  updateMusicEstimate();
  setStatus("Music panel prefilled from entry payload — review and Generate.", "ok");
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
  if (state.imageAbort) {
    state.imageAbort.abort(); // → image.cancelJob under the hood
    setStatus("Cancel requested…", "warn");
    return;
  }
  if (state.abort) {
    state.abort.abort(); // → video.cancelJob under the hood
    setStatus("Cancel requested…", "warn");
  }
}

/** Step 3: image.generate (sync) or image.generateAwait (job) → first frame → image-to-video.
 *
 * "Run first frame as job" (platform-media-generation-image-jobs.md §12.1): same
 * request body, but the host returns an ``ijob_…`` immediately and we await it
 * through the shared media_job event stream — the render survives an iframe
 * reload (``image.listJobs``), can be cancelled, and ``result.images[].url`` is
 * re-signed by ``image.getJob`` / ``image.resign`` after the 30-min TTL. */
async function onImageToVideo() {
  const prompt = el("prompt").value.trim();
  if (!prompt) {
    setStatus("Enter a prompt first.", "warn");
    return;
  }
  const asJob = el("image-as-job").checked && typeof anna.image.generateAwait === "function";
  setBusy(true);
  setStatus(asJob ? "Submitting first frame as image job…" : "Generating first frame via image.generate…");
  try {
    const args = {
      prompt: `Single cinematic keyframe, first frame of a video: ${prompt}`,
      n: 1,
    };
    let img;
    if (asJob) {
      state.imageAbort = new AbortController();
      const view = await anna.image.generateAwait(
        { ...args, clientTag: "first-frame", idempotencyKey: `first-frame:${hashPrompt(prompt)}` },
        {
          signal: state.imageAbort.signal,
          onProgress: (p) => {
            const q = p.progress && p.progress.queuePosition;
            setStatus(`First frame job ${p.state}${q != null ? ` · queue ${q}` : ""}…`);
          },
        }
      );
      state.imageJob = view;
      img = ((view.result && view.result.images) || [])[0];
      if (view.idempotentReplay) setStatus("↩ idempotent replay · 0 CU charged", "warn");
    } else {
      const res = await anna.image.generate(args);
      img = (res.images || [])[0];
    }
    if (!img || !img.url) throw new Error("image returned no image");
    state.lastFirstFrameUrl = img.url; // reusable as Lyria referenceImageUrl
    state.lastFirstFrameR2Key = img.r2Key || img.r2_key || null; // → image.resign after 30 min
    el("music-ref-from-frame-btn").hidden = false;
    setStatus("First frame ready — animating it…");
    setBusy(false);
    await onGenerate({ imageUrl: img.url });
  } catch (e) {
    setStatus(`Image → video failed: ${describeError(e)}`, "err");
    setBusy(false);
  } finally {
    state.imageAbort = null;
  }
}

function hashPrompt(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

// ─── music (platform-media-generation-music.md §12.2) ───────────────

async function loadMusicCatalog() {
  try {
    const res = await anna.llm.catalog({ serviceType: "music_gen" });
    state.musicCatalog = res.models || [];
    if (res.quota) state.remainingCU = res.quota.remainingCU;
  } catch (e) {
    setStatus(`llm.catalog(music_gen) failed: ${describeError(e)}`, "warn");
    state.musicCatalog = [];
  }
  fillModelSelect(el("music-model"), state.musicCatalog, "Default (admin/catalog pick)");
  for (const o of el("music-model").options) {
    const m = state.musicCatalog.find((x) => x.modelName === o.value);
    if (m && m.isPlatformDefault) o.textContent += " ◆";
  }
  syncMusicParamSpace();
  updateMusicEstimate();
}

function selectedMusicModel() {
  const name = el("music-model").value;
  return (
    state.musicCatalog.find((m) => m.modelName === name) ||
    state.musicCatalog.find((m) => m.isUserPreferred) ||
    state.musicCatalog.find((m) => m.isPlatformDefault) ||
    null
  );
}

/** The whole form is driven by `capabilities` — no model knowledge is
 *  hard-coded here (forum #426 "model-specific discovery"). */
function syncMusicParamSpace() {
  const m = selectedMusicModel();
  const caps = (m && m.capabilities) || {};
  const capsBox = el("music-caps");
  if (!m) {
    capsBox.textContent = "capabilities: —";
  } else {
    const d = caps.duration || {};
    capsBox.textContent =
      `capabilities: lyrics ${caps.lyrics ? `yes (${caps.lyricsMode})` : "no"}` +
      ` · vocals ${caps.vocals ? "yes" : "no"}` +
      ` · length ${d.type === "range" ? `${d.min}–${d.max}s` : `model-decided${d.typicalMaxSec ? ` ≤${d.typicalMaxSec}s` : ""}`}` +
      ` · ${(caps.formats || []).join("/")}` +
      (caps.referenceImage ? " · reference image" : "") +
      (caps.lyricsRequiredForVocals ? " · lyrics required for vocals" : "");
  }

  // lyrics: hidden when the model cannot take them; hint on prompt_embedded
  el("music-lyrics-row").hidden = !caps.lyrics;
  el("music-lyrics-hint").textContent =
    caps.lyricsMode === "prompt_embedded" ? "(model may rewrite — pick a lyricsMode=field model for verbatim singing)" : "";
  el("music-lyrics").maxLength = caps.lyricsMaxChars || 4000;

  // instrumental: instrumental-only models lock it on
  const inst = el("music-instrumental");
  if (caps.instrumentalOnly) {
    inst.checked = true;
    inst.disabled = true;
  } else {
    inst.disabled = !caps.instrumental;
  }
  el("music-lyrics").disabled = inst.checked;

  // duration: only range-length models expose the select
  const dur = el("music-duration");
  dur.innerHTML = "";
  const d = caps.duration || {};
  if (d.type === "range") {
    const step = d.max - d.min > 120 ? 15 : d.max - d.min > 30 ? 5 : 1;
    const seen = new Set();
    for (const v of [d.default, d.min, ...range(d.min, d.max, step), d.max]) {
      if (v == null || seen.has(v)) continue;
      seen.add(v);
      addOpt(dur, v, `${v}s${v === d.default ? " (default)" : ""}`);
    }
    dur.value = String(d.default);
    dur.disabled = false;
  } else {
    addOpt(dur, "", d.typicalMaxSec ? `model decides (≤${d.typicalMaxSec}s)` : "model decides");
    dur.disabled = true;
  }

  el("music-ref-row").hidden = !caps.referenceImage;
  el("music-ref-from-frame-btn").hidden = !state.lastFirstFrameUrl;
}

function range(min, max, step) {
  const out = [];
  for (let v = min; v <= max; v += step) out.push(v);
  return out;
}

/** Mirrors the catalog `pricing` block (per_song | per_second | per_minute
 *  rounded up) — the authoritative number comes back as estimatedCostCU. */
function updateMusicEstimate() {
  const m = selectedMusicModel();
  const box = el("music-estimate");
  if (!m || !m.pricing) {
    box.textContent = quotaSuffix("estimate: server-side");
    return;
  }
  const p = m.pricing;
  const caps = m.capabilities || {};
  const sec = Number(el("music-duration").value) || (caps.duration && caps.duration.default) || 0;
  let cu = null;
  if (p.unit === "per_song") cu = p.cuPerSong;
  else if (p.unit === "per_second" && sec) cu = p.cuPerSecond * Math.max(sec, p.minBilledSeconds || 0);
  else if (p.unit === "per_minute" && sec) cu = p.cuPerMinute * Math.max(Math.ceil(sec / 60), 1);
  const unitNote = p.unit === "per_minute" ? " (billed per whole minute)" : p.unit === "per_song" ? " per song" : "";
  box.textContent = quotaSuffix(cu != null ? `estimate: ~${cu} CU${unitNote}` : "estimate: server-side");
}

function collectMusicArgs(extra = {}) {
  const args = { prompt: el("music-prompt").value.trim(), ...extra };
  if (!args.prompt) throw Object.assign(new Error("Enter a music prompt first."), { code: "ui" });
  const m = selectedMusicModel();
  if (el("music-model").value) args.model = el("music-model").value;
  const caps = (m && m.capabilities) || {};
  const instrumental = el("music-instrumental").checked;
  if (!caps.instrumentalOnly) args.instrumental = instrumental;
  const lyrics = el("music-lyrics").value.trim();
  if (lyrics && !instrumental && !el("music-lyrics-row").hidden) args.lyrics = lyrics;
  if (!el("music-duration").disabled && el("music-duration").value) args.durationSec = Number(el("music-duration").value);
  const ref = el("music-ref").value.trim();
  if (ref && !el("music-ref-row").hidden) args.referenceImageUrl = ref;
  const idem = el("music-idem").value.trim();
  if (idem) args.idempotencyKey = idem;
  args.clientTag = `media-studio-score-${Date.now()}`;
  return args;
}

async function onMusicGenerate(extraArgs = {}) {
  let args;
  try {
    args = collectMusicArgs(extraArgs);
  } catch (e) {
    setStatus(e.message, "warn");
    return;
  }
  state.musicAbort = new AbortController();
  setMusicBusy(true);
  el("music-box").hidden = false;
  el("music-alternatives").hidden = true;
  el("music-meta-replay").hidden = true;
  showMusicProgress({ state: "queued" });
  setStatus("Submitting music job…");
  try {
    const view = await anna.music.generateAwait(args, {
      signal: state.musicAbort.signal,
      onProgress: (p) => {
        showMusicProgress(p);
        setStatus(p.state === "running" || p.phase === "IN_PROGRESS" ? "Composing…" : "Queued at provider…");
      },
    });
    state.musicJob = view;
    renderMusic(view);
    setStatus(
      view.idempotentReplay
        ? "Idempotent replay — existing job returned, nothing charged."
        : `Done — billed ${view.billedCostCU ?? "?"} CU.`,
      "ok"
    );
    pushHistory(view, `♪ ${args.prompt}`).catch(() => {});
  } catch (e) {
    hideMusicProgress();
    if (e.code === "cancelled") {
      setStatus("Music job cancelled — queued cancels are fully refunded.", "warn");
    } else if (e.code === "MUSIC_FEATURE_UNSUPPORTED") {
      setStatus(`Not supported by this model: ${e.message}`, "err");
      renderMusicAlternatives(e);
    } else {
      setStatus(`Music generation failed: ${describeError(e)}`, "err");
    }
    if (e.jobId) state.musicJob = { jobId: e.jobId, state: e.state || "failed" };
  } finally {
    state.musicAbort = null;
    setMusicBusy(false);
    refreshQuota();
  }
}

function onMusicCancel() {
  if (state.musicAbort) {
    state.musicAbort.abort(); // → music.cancelJob under the hood
    setStatus("Cancel requested…", "warn");
  }
}

/** -32165 arrives with `details.feature` / `details.alternatives` (facade
 *  computes them from the same catalog) — render as one-click model switch. */
function renderMusicAlternatives(e) {
  const d = (e.details && (e.details.data || e.details)) || {};
  const alts = Array.isArray(d.alternatives) ? d.alternatives : [];
  const box = el("music-alternatives");
  box.innerHTML = "";
  if (!alts.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.append(`Models that support "${d.feature || "this"}": `);
  for (const name of alts) {
    const b = document.createElement("button");
    b.textContent = name;
    b.addEventListener("click", () => {
      el("music-model").value = name;
      syncMusicParamSpace();
      updateMusicEstimate();
      setStatus(`Switched to ${name} — form inputs kept.`, "ok");
      box.hidden = true;
    });
    box.appendChild(b);
  }
}

function showMusicProgress(p) {
  el("music-progress-row").hidden = false;
  const fill = el("music-progress-fill");
  const running = p.state === "running" || p.phase === "IN_PROGRESS";
  fill.style.width = running ? "100%" : "10%";
  fill.classList.toggle("indeterminate", running);
  let label = running ? "composing" : p.state || "queued";
  if (!running && p.queuePosition != null) label += ` — position ${p.queuePosition} in queue`;
  el("music-progress-text").textContent = label;
}

function hideMusicProgress() {
  el("music-progress-row").hidden = true;
}

function renderMusic(view) {
  hideMusicProgress();
  const r = view.result || {};
  const player = el("music-player");
  if (r.url) {
    player.src = r.url;
    // 403 after the ~30 min TTL → re-sign through getJob
    player.onerror = () => onMusicResign();
    player.play().catch(() => {});
  }
  el("music-meta-model").textContent = `model: ${view.model || "default"}`;
  el("music-meta-duration").textContent = `length: ${r.durationSec != null ? `${r.durationSec}s` : "n/a"} · ${r.mimeType || ""}`;
  el("music-meta-cost").textContent = `cost: ${view.billedCostCU ?? "—"} CU`;
  el("music-meta-job").textContent = `job: ${view.jobId}`;
  el("music-meta-replay").hidden = !view.idempotentReplay;
  el("music-lyrics-out").hidden = !r.lyrics;
  el("music-lyrics-text").textContent = r.lyrics || "";
  el("persist-music-btn").disabled = !r.url;
}

async function onMusicResign() {
  if (!state.musicJob) return;
  try {
    const fresh = await anna.music.getJob({ jobId: state.musicJob.jobId });
    state.musicJob = fresh;
    if (fresh.state === "succeeded") renderMusic(fresh);
    setStatus("Music URL re-signed via music.getJob.", "ok");
  } catch (e) {
    setStatus(`music.getJob failed: ${describeError(e)}`, "err");
  }
}

async function onPersistMusic() {
  const r = state.musicJob && state.musicJob.result;
  if (!r || !r.url) return;
  setStatus("Fetching audio bytes for upload…");
  try {
    const resp = await fetch(r.url);
    if (!resp.ok) throw new Error(`fetch ${resp.status} — URL may have expired; it re-signs automatically on next play`);
    const ext = (r.mimeType || "").includes("wav") ? "wav" : "mp3";
    const uploaded = await persistBlob(await resp.blob(), {
      filename: `media-studio-${state.musicJob.jobId}.${ext}`,
      mime: r.mimeType || "audio/mpeg",
      metadata: { job_id: state.musicJob.jobId, kind: "music" },
    });
    setStatus(`Persisted → ${uploaded.r2_key || uploaded.download_url}`, "ok");
  } catch (e) {
    setStatus(`Persist failed: ${describeError(e)}`, "err");
  }
}

/** Error demo: lyrics sent to an instrumental-only model → -32165 with
 *  `alternatives`; the chips switch models without losing the prompt. */
async function onErrMusicFeature() {
  const instOnly = state.musicCatalog.find((m) => m.capabilities && m.capabilities.instrumentalOnly);
  if (!instOnly) {
    setStatus("No instrumental-only model in catalog — demo skipped.", "warn");
    return;
  }
  el("music-box").hidden = false;
  try {
    await anna.music.generate({ prompt: "demo", lyrics: "la la la", model: instOnly.modelName });
    setStatus("Unexpected: provider accepted lyrics.", "warn");
  } catch (e) {
    setStatus(`Expected error: ${describeError(e)}`, "ok");
    renderMusicAlternatives(e);
  }
}

/** Error demo: two generate calls with the same idempotencyKey — the second
 *  returns the first job (`idempotentReplay: true`) and holds no CU. */
async function onErrMusicReplay() {
  const key = `demo-replay-${Date.now()}`;
  const base = { prompt: "ambient pad for idempotency demo", instrumental: true, idempotencyKey: key };
  const cheapest = [...state.musicCatalog]
    .filter((m) => m.pricing && (m.pricing.cuPerSong != null || m.pricing.cuPerSecond != null))
    .sort((a, b) => (a.pricing.cuPerSong ?? a.pricing.cuPerSecond * 30) - (b.pricing.cuPerSong ?? b.pricing.cuPerSecond * 30))[0];
  if (cheapest) base.model = cheapest.modelName;
  if (cheapest && cheapest.capabilities.duration && cheapest.capabilities.duration.type === "range") base.durationSec = cheapest.capabilities.duration.min;
  try {
    const first = await anna.music.generate(base);
    const second = await anna.music.generate(base);
    setStatus(
      `first ${first.jobId} (held ${first.estimatedCostCU} CU) → second ${second.jobId} idempotentReplay=${second.idempotentReplay}`,
      second.idempotentReplay && second.jobId === first.jobId ? "ok" : "warn"
    );
    el("music-box").hidden = false;
    el("music-meta-job").textContent = `job: ${first.jobId}`;
    el("music-meta-replay").hidden = !second.idempotentReplay;
    // tidy up: cancel the demo job (queued → full refund)
    anna.music.cancelJob({ jobId: first.jobId }).catch(() => {});
  } catch (e) {
    setStatus(`Replay demo failed: ${describeError(e)}`, "err");
  } finally {
    refreshQuota();
  }
}

function setMusicBusy(busy) {
  el("music-generate-btn").disabled = busy;
  el("music-cancel-btn").disabled = !busy;
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
    // `mjob_` ids belong to the music namespace (video.getJob would 404).
    item.addEventListener("click", async () => {
      if (String(h.jobId).startsWith("mjob_")) {
        state.musicJob = { jobId: h.jobId };
        el("music-box").hidden = false;
        await onMusicResign();
        return;
      }
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
  // music panel
  el("music-generate-btn").addEventListener("click", () => onMusicGenerate());
  el("music-cancel-btn").addEventListener("click", onMusicCancel);
  el("persist-music-btn").addEventListener("click", onPersistMusic);
  el("err-music-feature-btn").addEventListener("click", onErrMusicFeature);
  el("err-music-replay-btn").addEventListener("click", onErrMusicReplay);
  el("music-model").addEventListener("change", () => {
    syncMusicParamSpace();
    updateMusicEstimate();
  });
  el("music-duration").addEventListener("change", updateMusicEstimate);
  el("music-instrumental").addEventListener("change", () => {
    const on = el("music-instrumental").checked;
    el("music-lyrics").disabled = on;
    if (on) el("music-lyrics").value = "";
  });
  el("music-idem-gen-btn").addEventListener("click", () => {
    el("music-idem").value = `ms-${Math.random().toString(16).slice(2, 10)}`;
  });
  el("music-ref-from-frame-btn").addEventListener("click", () => {
    if (state.lastFirstFrameUrl) el("music-ref").value = state.lastFirstFrameUrl;
  });
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
