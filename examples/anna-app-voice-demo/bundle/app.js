// Voice Demo — reference App for `anna.audio.session`
// (matrix-nexus docs/design/app-realtime-voice-session-and-executa-transcribe.md §5.2).
//
// Flow:
//   Start session  → anna.audio.session({llm, context, system}) + anna.audio.mic() + anna.audio.player()
//   Hold to talk   → mic frames → vs.sendAudio(pcm16)   (ONLY while pressed — ASR bills per second sent)
//   Server events  → asr_* (transcript), llm_* (reply text), tts_audio → player, tts_done → player.flush()
//   Barge-in       → interrupted → player.stop()
//   Set context    → vs.setContext(text)  (applies from the next utterance)
//   Say            → vs.say(text, {priority})  (TTS-only; say_start / say_done)
//   End            → vs.end() → usage summary

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), models: $("models"), open: $("btn-open"), end: $("btn-end"), llmAgent: $("llm-agent"),
  ptt: $("ptt"), vu: $("vu"), context: $("context"), setContext: $("btn-context"), tickContext: $("btn-context-tick"),
  contextState: $("context-state"), sayText: $("say-text"), sayPriority: $("say-priority"), say: $("btn-say"),
  interrupt: $("btn-interrupt"), transcript: $("transcript"), mTtfa: $("m-ttfa"), mSayTtfa: $("m-say-ttfa"),
  mChunks: $("m-chunks"), mUsage: $("m-usage"),
};

let anna = null;
let vs = null;       // VoiceSession
let mic = null;      // MicCapture
let player = null;   // VoiceAudioPlayer
let talking = false;
let chunks = 0;
let contextTimer = null;
let t = { asrFinal: 0, sayStart: 0, firstAudio: 0 };

const setStatus = (s) => (els.status.textContent = s);
/** Append a transcript bubble. Streaming updates are done by the caller on the returned element. */
function log(kind, text, { partial = false } = {}) {
  const div = document.createElement("div");
  div.className = `msg ${kind}${partial ? " partial" : ""}`;
  div.textContent = text;
  els.transcript.appendChild(div);
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return div;
}

function setSessionUi(open) {
  els.open.disabled = open;
  els.end.disabled = !open;
  els.ptt.disabled = !open;
  els.llmAgent.disabled = open;
  if (!open) els.ptt.classList.remove("active");
}

// ───────────────────────── session lifecycle ─────────────────────────

async function openSession() {
  setStatus("opening…");
  els.open.disabled = true;
  try {
    // Mic first (permission prompt) so a denied mic doesn't leave a dangling session.
    mic = anna.audio.mic({
      sampleRate: 16000,
      onChunk: (pcm16) => {
        if (!talking || !vs) return;
        chunks += vs.sendAudio(pcm16);
        els.mChunks.textContent = String(chunks);
      },
    });
    await mic.start();
    mic.setMuted(true); // push-to-talk gate; MicCapture also stops emitting while muted
    drawVu();

    vs = await anna.audio.session({
      llm: els.llmAgent.checked ? "agent" : "none",
      language: "zh",
      context: els.context.value.trim() || undefined,
      system: "You are a concise realtime voice assistant. Answer in one or two short sentences.",
      maxDurationSec: 1800,
    });
    player = anna.audio.player({ sampleRate: vs.audioConfig?.sample_rate || 24000 });
    player.configure(vs.audioConfig || {});
    player.onPlaybackEnd = () => vs && !vs.ended && setStatus("listening");
    wireEvents(vs);

    els.models.hidden = false;
    els.models.innerHTML = "";
    for (const [k, v] of Object.entries(vs.models || {})) {
      const s = document.createElement("span");
      s.textContent = `${k}: ${v}`;
      els.models.appendChild(s);
    }
    log("sys", `session ${vs.sessionId} started · max ${vs.maxDurationSec}s · llm=${els.llmAgent.checked ? "agent" : "none"}${vs.asrIdleTimeoutSec ? ` · ASR idle pause ${vs.asrIdleTimeoutSec}s` : ""}`);
    setStatus("listening");
    setSessionUi(true);
  } catch (e) {
    log("sys", `open failed: ${e.code || ""} ${e.message}`);
    setStatus("idle");
    cleanup();
    setSessionUi(false);
  }
}

function wireEvents(session) {
  // One streaming handle per side — the user's live transcript and Anna's
  // streaming reply must never share an element (an earlier version did, and
  // the next asr_partial overwrote Anna's finished bubble).
  let userEl = null;
  let replyEl = null;
  session.on("asr_partial", ({ text }) => {
    if (!userEl) userEl = log("user", "", { partial: true });
    userEl.textContent = text;
    els.transcript.scrollTop = els.transcript.scrollHeight;
  });
  session.on("asr_final", ({ text }) => {
    if (userEl) { userEl.textContent = text; userEl.classList.remove("partial"); userEl = null; }
    else log("user", text);
    t.asrFinal = performance.now();
    t.firstAudio = 0;
  });
  session.on("llm_start", () => {
    setStatus("thinking…");
    replyEl = null; // a new turn always gets a fresh bubble
  });
  session.on("llm_partial", ({ text }) => {
    if (!replyEl) replyEl = log("anna", "", { partial: true });
    replyEl.textContent += text;
    els.transcript.scrollTop = els.transcript.scrollHeight;
  });
  session.on("llm_done", ({ text }) => {
    if (replyEl) { replyEl.textContent = text; replyEl.classList.remove("partial"); replyEl = null; }
    else log("anna", text);
  });
  session.on("tts_audio", ({ audio }) => {
    if (!t.firstAudio) {
      t.firstAudio = performance.now();
      if (t.sayStart) { els.mSayTtfa.textContent = `${Math.round(t.firstAudio - t.sayStart)} ms`; t.sayStart = 0; }
      else if (t.asrFinal) els.mTtfa.textContent = `${Math.round(t.firstAudio - t.asrFinal)} ms`;
    }
    player.enqueue(audio);
    setStatus("speaking");
  });
  session.on("tts_done", () => player.flush());
  session.on("interrupted", ({ source }) => {
    player.stop();
    log("sys", `interrupted (${source})`);
    setStatus("listening");
  });
  session.on("say_start", ({ say_id }) => log("say", `▶ say ${say_id}`));
  session.on("say_done", ({ say_id, status }) => log("sys", `say ${say_id}: ${status}`));
  session.on("context_updated", ({ chars }) => (els.contextState.textContent = `context: ${chars} chars`));
  // Push-to-talk friendly: the host drops the ASR connection after
  // `asrIdleTimeoutSec` without audio and rebuilds it on the next frame.
  session.on("asr_paused", ({ idle_sec, reason }) => setStatus(`listening (ASR paused${idle_sec ? ` after ${idle_sec}s` : reason ? `: ${reason}` : ""})`));
  session.on("asr_resumed", ({ reconnect_ms }) => { log("sys", `ASR reconnected in ${reconnect_ms} ms`); setStatus("listening"); });
  session.on("quota_warning", ({ message }) => log("sys", `⚠ ${message}`));
  session.on("error", ({ code, message }) => log("sys", `error ${code}: ${message}`));
  session.on("ended", (usage) => {
    els.mUsage.textContent = `${usage.duration ?? "?"}s · asr ${usage.total_asr_seconds ?? "?"}s · tts ${usage.total_tts_characters ?? "?"} chars`;
    log("sys", `session ended${usage.reason ? ` (${usage.reason})` : ""}`);
    cleanup();
    setSessionUi(false);
    setStatus("idle");
  });
}

async function endSession() {
  if (!vs) return;
  setStatus("ending…");
  try { await vs.end(); } catch (e) { log("sys", `end failed: ${e.message}`); cleanup(); setSessionUi(false); setStatus("idle"); }
}

function cleanup() {
  pttUp();
  if (contextTimer) { clearInterval(contextTimer); contextTimer = null; els.tickContext.textContent = "Auto-update every 5s"; }
  if (mic) { mic.stop(); mic = null; }
  if (player) { player.destroy(); player = null; }
  vs = null;
  chunks = 0;
}

// ───────────────────────── push-to-talk ─────────────────────────
//
// Release must be unmissable: a stuck "talking" state keeps streaming (and
// billing) mic audio. So: capture the pointer on press, and treat pointerup
// anywhere, lost capture, window blur, tab hidden and Space keyup as release.
let pttPointerId = null;
const pttLabel = els.ptt.querySelector(".ptt__label");

function pttDown(e) {
  if (!vs || !mic || talking) return;
  if (e && typeof e.preventDefault === "function") e.preventDefault();
  if (e && e.pointerId != null) {
    pttPointerId = e.pointerId;
    try { els.ptt.setPointerCapture(e.pointerId); } catch (_) {}
  }
  talking = true;
  mic.setMuted(false);
  els.ptt.classList.add("active");
  pttLabel.textContent = "Listening… release to send";
}

function pttUp() {
  // Idempotent and unconditional on the UI side — never leave the button lit.
  const wasTalking = talking;
  talking = false;
  if (pttPointerId != null) {
    try { els.ptt.releasePointerCapture(pttPointerId); } catch (_) {}
    pttPointerId = null;
  }
  els.ptt.classList.remove("active");
  pttLabel.textContent = "Hold to talk";
  if (!wasTalking) return;
  if (mic) mic.setMuted(true);
  // Release → ask the host to finalize the sentence now (no trailing silence frames will follow).
  if (vs) vs.flushAudio();
}

els.ptt.addEventListener("pointerdown", (e) => { if (e.button === 0 || e.pointerType !== "mouse") pttDown(e); });
els.ptt.addEventListener("pointerup", pttUp);
els.ptt.addEventListener("pointercancel", pttUp);
els.ptt.addEventListener("lostpointercapture", pttUp);
// Fallbacks for releases the button never sees (pointer left the iframe, OS dialog, alt-tab…)
window.addEventListener("pointerup", () => { if (talking) pttUp(); }, true);
window.addEventListener("blur", pttUp);
document.addEventListener("visibilitychange", () => { if (document.hidden) pttUp(); });
// Space = push-to-talk only when focus is not on an interactive control
// (text fields, selects, buttons all have their own Space behaviour).
const isTypingTarget = () => {
  const el = document.activeElement;
  return !!el && el !== document.body && el !== els.ptt && ["TEXTAREA", "INPUT", "SELECT", "BUTTON"].includes(el.tagName);
};
window.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || isTypingTarget()) return;
  e.preventDefault();
  pttDown(e);
});
window.addEventListener("keyup", (e) => { if (e.code === "Space" && talking) { e.preventDefault(); pttUp(); } });

function drawVu() {
  const ctx = els.vu.getContext("2d");
  const buf = new Uint8Array(128);
  const frame = () => {
    if (!mic || !mic.analyser) { ctx.clearRect(0, 0, els.vu.width, els.vu.height); return; }
    mic.analyser.getByteTimeDomainData(buf);
    ctx.clearRect(0, 0, els.vu.width, els.vu.height);
    ctx.strokeStyle = talking ? "#34d399" : "#3b4a6b";
    ctx.beginPath();
    for (let i = 0; i < buf.length; i++) {
      const x = (i / buf.length) * els.vu.width;
      const y = (buf[i] / 255) * els.vu.height;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.stroke();
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// ───────────────────────── context / say / interrupt ─────────────────────────

async function applyContext() {
  if (!vs) return;
  try { await vs.setContext(els.context.value); } catch (e) { log("sys", `setContext: ${e.code || ""} ${e.message}`); }
}
els.setContext.addEventListener("click", applyContext);
els.tickContext.addEventListener("click", () => {
  if (contextTimer) { clearInterval(contextTimer); contextTimer = null; els.tickContext.textContent = "Auto-update every 5s"; return; }
  let tick = 0;
  contextTimer = setInterval(() => {
    if (!vs) return;
    tick += 1;
    // Simulates a game digest that changes every few seconds (the GG use case).
    els.context.value = JSON.stringify({ minute: 10 + tick, gold: 1500 + tick * 120, hp: `${Math.max(10, 90 - tick * 7)}%`, enemies_missing: tick % 2 ? ["Anti-Mage"] : [] });
    applyContext();
  }, 5000);
  els.tickContext.textContent = "Stop auto-update";
});
els.say.addEventListener("click", async () => {
  if (!vs) return;
  const priority = els.sayPriority.value;
  t.sayStart = performance.now();
  t.firstAudio = 0;
  try { await vs.say(els.sayText.value, { priority, interrupt: priority === "high" }); }
  catch (e) { log("sys", `say: ${e.code || ""} ${e.message}`); }
});
els.interrupt.addEventListener("click", async () => {
  if (!vs) return;
  player?.stop();
  try { await vs.interrupt(); } catch (_) {}
  setStatus("listening");
});
els.open.addEventListener("click", openSession);
els.end.addEventListener("click", endSession);
window.addEventListener("pagehide", () => { if (vs) vs.end().catch(() => {}); });

// ───────────────────────── boot ─────────────────────────

(async () => {
  try {
    anna = await AnnaAppRuntime.connect();
    await anna.window.set_title({ title: "Voice Demo" }).catch(() => {});
    // Mobile shell relays the RN AppState (iOS suspends getUserMedia + timers
    // in a backgrounded WKWebView): release the mic and end the session
    // cleanly instead of letting the socket die mid-stream.
    anna.on("host.app_state", (p) => {
      const st = p && p.state;
      if (st === "background" || st === "inactive") {
        pttUp();
        if (vs) endSession().catch(() => {});
      }
    });
    if (typeof anna.audio?.session !== "function") {
      log("sys", "This host's SDK has no anna.audio.session (needs @anna-ai/app-runtime ≥ 0.21.0).");
      return;
    }
    setStatus("idle — click Start session");
  } catch (e) {
    setStatus(`connect failed: ${e.message}`);
  }
})();
