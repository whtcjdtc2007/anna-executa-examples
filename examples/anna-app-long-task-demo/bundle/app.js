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

// ── 4. Concurrency burst (forum #474) ─────────────────────────────────
// N parallel invokeAsyncAwait calls under a separate clientTag so section 2's
// reload recovery ignores them. Three outcomes are tallied live:
//   • create rejected  → err.code === "job_quota_exceeded" (details.active/max_active)
//   • queued on agent  → progress event type "queued" (data.position)
//   • terminal         → succeeded | failed(code) | cancelled | expired
// listJobs is polled every 2s for the host-side queued/running split.
const BURST_TAG = "long-task-demo-burst";
const burstLog = $("burst-log");
const burstStats = $("burst-stats");
let burstAbort = null;

function renderBurstStats(s) {
  burstStats.textContent =
    `created ${s.created}/${s.total} · quota_exceeded ${s.quota} · ` +
    `host queued ${s.hostQueued} running ${s.hostRunning} · ` +
    `agent-queued events ${s.queuedEvents} · ` +
    `succeeded ${s.succeeded} failed ${s.failed} cancelled ${s.cancelled} ` +
    `(long_job_capacity ${s.capacity}) · ${s.elapsed}s`;
}

async function runBurst() {
  const anna = await annaReady;
  const n = Math.max(1, Number($("burst-n").value) || 12);
  const steps = Number($("burst-steps").value) || 3;
  const stepSeconds = Number($("burst-step-seconds").value) || 1;
  burstLog.textContent = "";
  $("burst-run").disabled = true;
  $("burst-cancel").disabled = false;
  burstAbort = new AbortController();
  const t0 = Date.now();
  const s = {
    total: n, created: 0, quota: 0, hostQueued: 0, hostRunning: 0,
    queuedEvents: 0, succeeded: 0, failed: 0, cancelled: 0, capacity: 0,
    elapsed: 0,
  };
  renderBurstStats(s);

  // Host-side split via listJobs (authoritative; the SDK's own polling is
  // per job, this is one call for the whole batch).
  const poll = setInterval(async () => {
    try {
      const { jobs } = await anna.tools.listJobs({
        clientTag: BURST_TAG, state: ["queued", "running"], limit: 100,
      });
      s.hostQueued = jobs.filter((j) => j.state === "queued").length;
      s.hostRunning = jobs.filter((j) => j.state === "running").length;
    } catch (_) {}
    s.elapsed = Math.round((Date.now() - t0) / 1000);
    renderBurstStats(s);
  }, 2000);

  const one = async (i) => {
    try {
      await anna.tools.invokeAsyncAwait(
        {
          tool_id: EXECUTA_TOOL_ID,
          method: "run_steps",
          args: { steps, step_seconds: stepSeconds },
          // Queue time counts against the deadline — give the whole batch
          // room: N jobs / 30 slots rounds, min 60s.
          timeoutMs: Math.max(60_000, Math.ceil(steps * stepSeconds * 1000 * (Math.ceil(n / 30) + 1) * 1.5)),
          clientTag: BURST_TAG,
        },
        {
          signal: burstAbort.signal,
          onProgress: (ev) => {
            if (ev.type === "queued") {
              s.queuedEvents += 1;
              log(burstLog, `#${i} queued on agent — position ${ev.data && ev.data.position} / slots ${ev.data && ev.data.max_concurrent}`);
            }
          },
        },
      );
      s.succeeded += 1;
    } catch (err) {
      if (err.code === "job_quota_exceeded") {
        s.quota += 1;
        log(burstLog, `#${i} create rejected: job_quota_exceeded ${JSON.stringify(err.details || {})}`, "err");
        return; // never created
      }
      if (err.code === "cancelled") s.cancelled += 1;
      else {
        s.failed += 1;
        if (err.code === "long_job_capacity") s.capacity += 1;
        log(burstLog, `#${i} ${err.code}: ${err.message} ${JSON.stringify(err.details || {})}`, "err");
      }
    }
  };

  // invokeAsyncAwait creates the job synchronously inside; count creations
  // as the calls settle (a rejected create never increments).
  const tasks = Array.from({ length: n }, (_, i) =>
    one(i + 1).then(() => { /* settled */ }),
  );
  // Rough "created" = total − quota rejections, refreshed as they come in.
  const tick = setInterval(() => { s.created = n - s.quota; renderBurstStats(s); }, 500);
  await Promise.allSettled(tasks);
  clearInterval(tick);
  clearInterval(poll);
  s.created = n - s.quota;
  s.hostQueued = 0;
  s.hostRunning = 0;
  s.elapsed = Math.round((Date.now() - t0) / 1000);
  renderBurstStats(s);
  log(burstLog, `done in ${s.elapsed}s — ${JSON.stringify(s)}`, "ok");
  $("burst-run").disabled = false;
  $("burst-cancel").disabled = true;
  burstAbort = null;
}

$("burst-run").onclick = () => runBurst().catch((err) => log(burstLog, `burst failed: ${err.message}`, "err"));
$("burst-cancel").onclick = () => burstAbort && burstAbort.abort();

recover().catch((err) => log(recoverLog, `recover failed: ${err.message}`, "err"));
