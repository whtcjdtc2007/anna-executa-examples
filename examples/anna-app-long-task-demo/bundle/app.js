// Long Task Demo — anna.tools.invokeAsync best practices, four scenarios:
//
//   1. invokeAsyncAwait + onProgress    → live progress bar for a
//      multi-minute tool; result resolves the promise.
//   2. AbortSignal → cancelJob          → cooperative cancel; idempotent.
//   3. Reload recovery                  → listJobs({clientTag}) + getJob
//      re-adopt an in-flight job after the iframe reloads.
//   4. Sync contrast                    → the same tool through plain
//      tools.invoke with a 150s budget → structured tool_timeout whose
//      details carry requested_timeout_ms / max_timeout_ms (the 90s edge
//      ceiling, forum #199).
//
// Loaded as a native ES module (SDK >= 0.15.0 for the invokeAsync family).

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Bundled-executa handle → concrete tool_id resolution (same pattern as
// anna-app-aps-files-demo): publish/dev writes bundle/anna-tool-ids.js;
// fall back to the local dev id from executas/.../executa.json.
const DEV_FALLBACK_TOOL_ID = "tool-test-long-task-engine-12345678";
const EXECUTA_TOOL_ID =
  (typeof window !== "undefined"
    && window.__ANNA_TOOL_IDS__
    && window.__ANNA_TOOL_IDS__["long-task-engine"])
  || DEV_FALLBACK_TOOL_ID;

// Stable clientTag → reload recovery can find OUR jobs and nobody else's.
const CLIENT_TAG = "long-task-demo";

const $ = (id) => document.getElementById(id);
const jobLog = $("job-log");
const recoverLog = $("recover-log");
const syncLog = $("sync-log");
const bar = $("bar");
const barLabel = $("bar-label");
const runBtn = $("run-async");
const cancelBtn = $("cancel");

function log(el, text, cls) {
  const line = document.createElement("span");
  if (cls) line.className = cls;
  line.textContent = text + "\n";
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function setBar(step, total, note) {
  const pct = total ? Math.round((step / total) * 100) : 0;
  bar.style.width = `${pct}%`;
  barLabel.textContent = note ? `${pct}% — ${note}` : `${pct}%`;
}

const annaReady = (async () => {
  const anna = await AnnaAppRuntime.connect();
  window.anna = anna;
  anna.window.set_title({ title: "Long Task Demo" }).catch(() => {});
  return anna;
})().catch((err) => {
  log(jobLog, `runtime.connect failed: ${err.message}`, "err");
  throw err;
});

let abortCtl = null;

// Shared progress renderer — used by both the fresh-run path (onProgress)
// and the reload-recovery path (getJob progress slices).
function renderProgressEvent(ev) {
  const d = ev.data || {};
  if (ev.type === "tool_update" && d.step != null) {
    setBar(d.step, d.total || 0, d.note);
    log(jobLog, `progress seq=${ev.seq} step ${d.step}/${d.total} ${d.note || ""}`);
  } else if (ev.type === "tool_start") {
    log(jobLog, `tool_start seq=${ev.seq}`);
  } else {
    log(jobLog, `${ev.type} seq=${ev.seq} ${JSON.stringify(d).slice(0, 120)}`);
  }
}

async function runAsync() {
  const anna = await annaReady;
  const steps = Number($("steps").value) || 12;
  const stepSeconds = Number($("step-seconds").value) || 5;
  const failMid = $("fail-mid").checked;

  jobLog.textContent = "";
  setBar(0, steps, "queued…");
  runBtn.disabled = true;
  cancelBtn.disabled = false;
  abortCtl = new AbortController();

  const args = { steps, step_seconds: stepSeconds };
  if (failMid) args.fail_at_step = 5;

  try {
    const result = await anna.tools.invokeAsyncAwait(
      {
        tool_id: EXECUTA_TOOL_ID,
        method: "run_steps",
        args,
        // Budget: generous margin over steps*stepSeconds, min 60s (policy
        // floor). Calls that fit in <90s should use plain tools.invoke.
        timeoutMs: Math.max(60_000, Math.ceil(steps * stepSeconds * 1000 * 1.5)),
        clientTag: CLIENT_TAG,
      },
      {
        onProgress: renderProgressEvent,
        signal: abortCtl.signal,
      },
    );
    setBar(steps, steps, "done");
    log(jobLog, `✔ succeeded: ${JSON.stringify(result)}`, "ok");
  } catch (err) {
    // err.code ∈ tool_failed | tool_timeout | cancelled | wait_timeout …
    // err.jobId lets you recover a wait_timeout'ed job via getJob.
    setBar(0, 1, err.code || "error");
    log(
      jobLog,
      `✘ ${err.code || "error"}: ${err.message}` +
        (err.jobId ? ` (jobId=${err.jobId})` : "") +
        (err.details ? ` details=${JSON.stringify(err.details)}` : ""),
      "err",
    );
    if (err.code === "cancelled") {
      // Demonstrate idempotency: a second cancel is a no-op.
      const again = await anna.tools.cancelJob({ jobId: err.jobId }).catch(() => null);
      if (again) log(jobLog, `second cancelJob → cancelled:${again.cancelled} (idempotent)`);
    }
  } finally {
    runBtn.disabled = false;
    cancelBtn.disabled = true;
    abortCtl = null;
  }
}

// ── 2. Reload recovery ─────────────────────────────────────────────────
// On boot: find our in-flight jobs and re-attach. Progress continues from
// the job's lastSeq; terminal state resolves exactly like a fresh run.
async function recover() {
  const anna = await annaReady;
  let out;
  try {
    out = await anna.tools.listJobs({
      clientTag: CLIENT_TAG,
      state: ["queued", "running"],
    });
  } catch (err) {
    if (err.code === "not_implemented") {
      log(recoverLog, "job channel not available on this host (not_implemented)");
      return;
    }
    throw err;
  }
  if (!out.jobs.length) {
    log(recoverLog, "no in-flight jobs to recover (start one above, then reload)");
    return;
  }
  for (const job of out.jobs) {
    log(recoverLog, `re-adopting ${job.jobId} (state=${job.state}, lastSeq=${job.lastSeq})`, "ok");
    runBtn.disabled = true;
    cancelBtn.disabled = false;
    abortCtl = new AbortController();
    cancelBtn.onclick = () => abortCtl && abortCtl.abort();
    pollAdopted(anna, job.jobId, 0).finally(() => {
      runBtn.disabled = false;
      cancelBtn.disabled = true;
    });
  }
}

async function pollAdopted(anna, jobId, sinceSeq) {
  // Event-driven adoption is also possible via anna.tools.onJobEvent; the
  // poll loop keeps the recovery example self-contained.
  let seq = sinceSeq;
  for (;;) {
    if (abortCtl && abortCtl.signal.aborted) {
      await anna.tools.cancelJob({ jobId, reason: "aborted after reload" }).catch(() => {});
    }
    const snap = await anna.tools.getJob({ jobId, sinceSeq: seq });
    for (const ev of snap.progress) renderProgressEvent(ev);
    if (snap.progress.length) seq = snap.lastSeq;
    if (["succeeded", "failed", "cancelled", "expired"].includes(snap.state)) {
      if (snap.state === "succeeded") {
        setBar(1, 1, "done");
        log(jobLog, `✔ recovered job succeeded: ${JSON.stringify(snap.result)}`, "ok");
      } else {
        log(jobLog, `✘ recovered job ${snap.state}: ${snap.error ? snap.error.message : ""}`, "err");
      }
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// ── 3. Sync contrast ───────────────────────────────────────────────────
async function runSyncContrast() {
  const anna = await annaReady;
  syncLog.textContent = "";
  log(syncLog, "tools.invoke run_steps(steps=30, step_seconds=5) with timeoutMs=150000 …");
  const t0 = Date.now();
  try {
    const out = await anna.tools.invoke(
      {
        tool_id: EXECUTA_TOOL_ID,
        method: "run_steps",
        args: { steps: 30, step_seconds: 5 }, // 150s of work
        timeoutMs: 150_000, // will be clamped host-side to 90_000
      },
      { timeoutMs: 170_000 }, // SDK-side postMessage wait > host budget
    );
    log(syncLog, `unexpected success after ${Date.now() - t0}ms: ${JSON.stringify(out)}`);
  } catch (err) {
    log(
      syncLog,
      `✔ expected failure after ${Math.round((Date.now() - t0) / 1000)}s — ` +
        `${err.code}: ${err.message}\ndetails: ${JSON.stringify(err.details || {})}`,
      "ok",
    );
    log(syncLog, "→ details.requested_timeout_ms/max_timeout_ms show the silent-looking clamp; use invokeAsync instead.");
  }
}

runBtn.onclick = runAsync;
cancelBtn.onclick = () => abortCtl && abortCtl.abort();
$("run-sync").onclick = runSyncContrast;

recover().catch((err) => log(recoverLog, `recover failed: ${err.message}`, "err"));
