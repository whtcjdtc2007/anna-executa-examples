/**
 * Focus Flow — Anna App bundle controller
 *
 * Connects to Anna via the runtime SDK loaded from
 *   /static/anna-apps/_sdk/0.1.0/index.js   (global: AnnaAppRuntime)
 *
 * Verified against matrix-nexus:
 *   - SDK:        static/anna-apps/_sdk/0.1.0/index.js
 *   - Dispatcher: src/services/anna_app_rpc_dispatcher.py
 *   - ACL:        src/services/anna_app_runtime_service.py::host_api_allows
 *
 * Real RPC shapes used here (do not change without re-checking dispatcher):
 *   anna.tools.invoke({
 *     tool_id: "<server-minted ID, e.g. tool-{handle}-focus-session-{uniq}>",
 *     method: "session",
 *     args: { action, ... },
 *   })
 *   anna.storage.get({ key })                         → { value }
 *   anna.storage.set({ key, value })
 *   anna.chat.write_message({ role, content })
 *   anna.window.set_title({ title })
 *
 * window.ready is auto-emitted by AnnaAppRuntime.connect(); no need to call it.
 *
 * NOTE: ``TOOL_ID`` is a placeholder. Mint your own ID at
 *   https://anna.partners/executa  → My Tools → Create Tool → 🪪 Mint
 * then paste the minted string here AND into manifest.json's required_executas
 * + ui.host_api.tools entries (they must match exactly).
 */

// Replace this with the actual ID minted on https://anna.partners/executa.
const TOOL_ID = "tool-test-focus-session-12345678";
// Method on the running plugin (matches `tool` in the plugin's describe()).
const TOOL_METHOD = "session";
const STORAGE_KEY = "focus-flow:last-topic";

const PRESETS = [15, 25, 45, 60];
const DEFAULT_DURATION = 25;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const els = {
  topic:        $("#topic-input"),
  primaryBtn:   $("#primary-btn"),
  resetBtn:     $("#reset-btn"),
  coachBtn:     $("#coach-btn"),
  themeToggle:  $("#theme-toggle"),
  timeDisplay:  $("#time-display"),
  statusLabel:  $("#status-label"),
  ringProgress: document.querySelector(".ring__progress"),
  todaySummary: $("#today-summary"),
  historyList:  $("#history-list"),
  connStatus:   $("#conn-status"),
  presets:      $$(".chip[data-minutes]"),
  body:         document.body,
};

let anna = null;
let state = { active: null, today: null, recent: [] };
let chosenMinutes = DEFAULT_DURATION;
let tickHandle = null;
let isCalling = false;

const ARC_R = 96; // matches r="96" in index.html
const ARC_CIRCUMFERENCE = 2 * Math.PI * ARC_R;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

async function init() {
  // Prepare ring stroke (length = full arc).
  if (els.ringProgress) {
    els.ringProgress.style.strokeDasharray = String(ARC_CIRCUMFERENCE);
    els.ringProgress.style.strokeDashoffset = String(ARC_CIRCUMFERENCE);
  }
  bindUi();
  applyPreset(DEFAULT_DURATION);
  honorSystemTheme();
  renderEmpty();

  // Connect to Anna. Falls back to standalone preview when SDK / wid+t missing.
  try {
    if (typeof AnnaAppRuntime === "undefined") {
      throw new Error("AnnaAppRuntime SDK not loaded");
    }
    anna = await AnnaAppRuntime.connect();
    setConn(true);
  } catch (e) {
    setConn(false);
    setStatus("Standalone preview");
    console.warn("[focus-flow] running standalone:", e?.message || e);
    return;
  }

  // Restore last topic (best-effort).
  try {
    const result = await anna.storage.get({ key: STORAGE_KEY });
    const value = result?.value;
    if (typeof value === "string" && value) els.topic.value = value;
  } catch {
    /* storage may be denied / empty */
  }

  await refreshState();
  startTicker();
}

// ---------------------------------------------------------------------------
// Tool RPC helpers
// ---------------------------------------------------------------------------

async function callSession(action, extra = {}) {
  if (!anna) throw new Error("not connected");
  if (isCalling) return null;
  isCalling = true;
  setBusy(true);
  try {
    return await anna.tools.invoke({
      tool_id: TOOL_ID,
      method: TOOL_METHOD,
      args: { action, ...extra },
    });
  } catch (e) {
    setStatus(`Error: ${e?.message || e}`, "error");
    throw e;
  } finally {
    isCalling = false;
    setBusy(false);
  }
}

async function refreshState() {
  if (!anna) return;
  try {
    const result = await callSession("get_state");
    if (result) applyState(result);
  } catch {
    /* already surfaced */
  }
}

// ---------------------------------------------------------------------------
// Actions wired to the single primary button (label changes by state)
// ---------------------------------------------------------------------------

async function onPrimaryClick() {
  const a = state.active;
  if (!a) return startSession();
  if (a.status === "running") return pauseSession();
  if (a.status === "paused") return resumeSession();
}

async function startSession() {
  const topic = els.topic.value.trim();
  const duration = clampMinutes(chosenMinutes);
  try {
    const result = await callSession("start", {
      duration_minutes: duration,
      topic,
    });
    if (!result) return;
    if (anna && topic) {
      try {
        await anna.storage.set({ key: STORAGE_KEY, value: topic });
      } catch {
        /* non-fatal */
      }
    }
    applyState({ active: result.active, today: state.today, recent: state.recent });
    if (anna) {
      try {
        await anna.chat.write_message({
          role: "user",
          content: topic
            ? `Started a ${duration}-minute focus session on "${topic}".`
            : `Started a ${duration}-minute focus session.`,
        });
      } catch {
        /* chat may be denied */
      }
    }
  } catch {
    /* error already shown */
  }
}

async function pauseSession() {
  const result = await callSession("pause");
  if (result) applyState({ active: result.active, today: state.today, recent: state.recent });
}

async function resumeSession() {
  const result = await callSession("resume");
  if (result) applyState({ active: result.active, today: state.today, recent: state.recent });
}

async function completeSession() {
  try {
    const result = await callSession("complete");
    if (!result) return;
    if (result.completed && anna) {
      const minutes = Math.round(result.completed.focused_seconds / 60);
      try {
        await anna.chat.write_message({
          role: "user",
          content: `Completed focus session (${minutes} min).`,
        });
      } catch {
        /* non-fatal */
      }
    }
    await refreshState();
  } catch {
    /* error already shown */
  }
}

async function askCoach() {
  if (!anna) return;
  try {
    await anna.chat.write_message({
      role: "user",
      content: "Coach, how should I make the most of this focus block?",
    });
  } catch (e) {
    setStatus(`Chat denied: ${e?.message || e}`, "error");
  }
}

// ---------------------------------------------------------------------------
// State application + render
// ---------------------------------------------------------------------------

function applyState(next) {
  state = {
    active: next.active ?? null,
    today: next.today ?? state.today,
    recent: Array.isArray(next.recent) ? next.recent : state.recent,
  };
  render();
}

function render() {
  renderActive();
  renderTotals();
  renderHistory();
  syncWindowTitle();
}

function renderActive() {
  const a = state.active;
  if (!a) {
    renderEmpty();
    return;
  }
  els.body.dataset.state = a.status;
  setStatus(a.status === "running" ? "Focusing" : "Paused");
  const remaining = Math.max(0, (a.remaining_seconds ?? a.duration_seconds) | 0);
  els.timeDisplay.textContent = formatClock(remaining);
  const total = Math.max(1, a.duration_seconds | 0);
  const elapsed = Math.max(0, total - remaining);
  if (els.ringProgress) {
    els.ringProgress.style.strokeDashoffset = String(
      ARC_CIRCUMFERENCE * (1 - elapsed / total),
    );
  }
  els.primaryBtn.textContent = a.status === "running" ? "Pause" : "Resume";
  els.primaryBtn.disabled = false;
  if (els.resetBtn) {
    els.resetBtn.hidden = false;
    els.resetBtn.disabled = false;
  }
  els.topic.value = a.topic || els.topic.value;
  els.topic.disabled = true;
  setPresetDisabled(true);
}

function renderEmpty() {
  els.body.dataset.state = "idle";
  setStatus("Ready");
  els.timeDisplay.textContent = formatClock(chosenMinutes * 60);
  if (els.ringProgress) {
    els.ringProgress.style.strokeDashoffset = String(ARC_CIRCUMFERENCE);
  }
  els.primaryBtn.textContent = "Start";
  els.primaryBtn.disabled = false;
  if (els.resetBtn) {
    els.resetBtn.hidden = true;
  }
  els.topic.disabled = false;
  setPresetDisabled(false);
}

function renderTotals() {
  const t = state.today || { session_count: 0, focused_minutes: 0 };
  const count = t.session_count ?? 0;
  const minutes = t.focused_minutes ?? 0;
  els.todaySummary.textContent =
    `${count} session${count === 1 ? "" : "s"} · ${minutes} min`;
}

function renderHistory() {
  const items = state.recent || [];
  els.historyList.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "history__empty";
    li.textContent = "No sessions yet — start your first block above.";
    els.historyList.appendChild(li);
    return;
  }
  for (const h of items.slice(0, 8)) {
    const li = document.createElement("li");
    li.className = "history__item";
    const minutes = Math.round((h.focused_seconds || 0) / 60);
    const when = new Date((h.completed_at || 0) * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const topic = document.createElement("span");
    topic.className = "history__topic";
    topic.textContent = h.topic || "Untitled";
    const meta = document.createElement("span");
    meta.className = "history__meta muted";
    meta.textContent = `${minutes} min · ${when}`;
    li.append(topic, meta);
    els.historyList.appendChild(li);
  }
}

function syncWindowTitle() {
  if (!anna) return;
  const a = state.active;
  let title = "Focus Flow";
  if (a) {
    const remaining = Math.max(0, (a.remaining_seconds ?? a.duration_seconds) | 0);
    const topic = a.topic ? ` · ${a.topic}` : "";
    title = `${formatClock(remaining)}${topic} — Focus Flow`;
  }
  // window.set_title is the only window allow-listed method we use; ACL OK.
  anna.window.set_title({ title }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Ticker — extrapolate locally between authoritative get_state polls
// ---------------------------------------------------------------------------

function startTicker() {
  if (tickHandle) return;
  tickHandle = setInterval(() => {
    const a = state.active;
    if (!a || a.status !== "running") return;
    if (typeof a.remaining_seconds !== "number") return;
    a.remaining_seconds = Math.max(0, a.remaining_seconds - 1);
    a.focused_seconds = (a.focused_seconds || 0) + 1;
    if (a.remaining_seconds === 0) {
      // Re-poll authoritative state on rollover.
      refreshState().catch(() => {});
    } else {
      renderActive();
      syncWindowTitle();
    }
  }, 1000);

  // Re-sync from server every 30 s.
  setInterval(() => {
    if (state.active) refreshState().catch(() => {});
  }, 30_000);
}

// ---------------------------------------------------------------------------
// UI binding + utilities
// ---------------------------------------------------------------------------

function bindUi() {
  els.primaryBtn.addEventListener("click", onPrimaryClick);
  els.resetBtn?.addEventListener("click", completeSession);
  els.coachBtn?.addEventListener("click", askCoach);
  els.themeToggle?.addEventListener("click", toggleTheme);
  for (const chip of els.presets) {
    chip.addEventListener("click", () => {
      const m = parseInt(chip.dataset.minutes || "25", 10);
      applyPreset(m);
    });
  }
}

function applyPreset(minutes) {
  chosenMinutes = clampMinutes(minutes);
  for (const chip of els.presets) {
    chip.classList.toggle(
      "is-active",
      parseInt(chip.dataset.minutes || "0", 10) === chosenMinutes,
    );
  }
  if (!state.active) {
    els.timeDisplay.textContent = formatClock(chosenMinutes * 60);
  }
}

function setPresetDisabled(disabled) {
  for (const chip of els.presets) chip.disabled = !!disabled;
}

// ---------------------------------------------------------------------------
// Theme — CSS uses `[data-theme="light"]` / `[data-theme="dark"]` on <html>.
// Defaults: CSS picks dark unless the OS is in light mode (handled by the
// `prefers-color-scheme` media query in style.css). The toggle records an
// explicit override that wins over both the system pref and the default.
// We keep the override in localStorage so the choice survives reloads.
// ---------------------------------------------------------------------------

const THEME_STORAGE_KEY = "focusflow:theme";

function effectiveTheme() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function applyTheme(theme) {
  // Only "light" / "dark" are valid; anything else clears the override and
  // hands control back to the system pref via CSS.
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* storage may be denied in sandboxed iframes; non-fatal */
  }
}

function honorSystemTheme() {
  // Restore an explicit user choice if there is one. Otherwise leave
  // `data-theme` unset so the CSS media query drives the look automatically.
  let saved = null;
  try {
    saved = localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    /* non-fatal */
  }
  if (saved === "light" || saved === "dark") applyTheme(saved);
}

function setBusy(on) {
  els.body.classList.toggle("is-busy", !!on);
}

function setConn(on) {
  if (!els.connStatus) return;
  els.connStatus.classList.toggle("dot--off", !on);
  els.connStatus.classList.toggle("dot--on", !!on);
  els.connStatus.title = on ? "Connected to Anna" : "Disconnected";
}

function setStatus(text, kind) {
  if (!els.statusLabel) return;
  els.statusLabel.textContent = text;
  if (kind) els.statusLabel.dataset.kind = kind;
  else delete els.statusLabel.dataset.kind;
}

function clampMinutes(m) {
  if (!Number.isFinite(m)) return DEFAULT_DURATION;
  return Math.max(1, Math.min(180, m | 0));
}

function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

document.addEventListener("DOMContentLoaded", init);
