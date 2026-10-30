# Long Task Demo (`anna-app-long-task-demo`)

[中文](./README.zh-CN.md)

The official demo for the **async tool-job channel**
(`anna.tools.invokeAsync`, design:
matrix-nexus `docs/design/anna-app-tools-invoke-async-jobs.md`). It shows the
four things every long-task Anna App needs:

1. **`invokeAsyncAwait` + live progress** — start a multi-minute tool with one
   call; the bundled `long-task-engine` Executa emits an `executa/progress`
   notification per step and the UI renders a progress bar via `onProgress`.
2. **Cancel** — an `AbortSignal` wired to the Cancel button triggers
   `cancelJob`. Cancel is idempotent: a second cancel returns
   `cancelled: false`.
3. **Reload recovery** — refresh the window mid-job: on boot the app calls
   `listJobs({clientTag: "long-task-demo", state: ["queued","running"]})` and
   re-adopts the in-flight job via `getJob({sinceSeq})`. Job state lives on
   the host, not in the iframe.
4. **The sync contrast** — the same tool through plain `tools.invoke` with a
   150s budget: the platform clamps the wait to **90s** and returns a
   structured `tool_timeout` whose `details` carry `requested_timeout_ms` /
   `max_timeout_ms`. Past the public edge's held-request tolerance (~100s) a
   sync result physically cannot reach the browser — that is *why* the async
   channel exists.

## Decision table

| Situation | Channel |
| --- | --- |
| Tool finishes in < 90s | `tools.invoke` (simplest) |
| Tool may exceed 90s, or you don't want to block | `tools.invokeAsync` / `invokeAsyncAwait` |
| Need progress UI / cancel / survive reloads | `tools.invokeAsync` family only |

## Run it

```bash
cd examples/anna-app-long-task-demo
pnpm install          # or npm i — only pulls @anna-ai/cli
npx anna-app dev      # real local run: plugin + in-process job table
```

- Set `steps` / `step_seconds`, click **Run async**, watch the bar.
- Click **Cancel** mid-run; run again and reload the page mid-run to see
  recovery.
- Click the sync-contrast button and read the structured timeout.
- `npx anna-app test` replays `fixtures/happy-path.jsonl` (sync fast path).

The local harness implements the full job lifecycle in process
(`anna-app-runtime-local >= 0.2.0a20`); the one difference from production is
that jobs are not persisted across harness restarts.

## Plugin-side progress (copy this into your Executa)

```python
from executa_sdk import bind_invoke, emit_progress

def handle_invoke(req_id, params):
    with bind_invoke(params):                 # correlates events to THIS invoke
        for i in range(1, total + 1):
            do_step(i)
            emit_progress("tool_update", {"step": i, "total": total})
```

Semantics (host side, silent-drop by design):

- `type` ∈ `progress` | `tool_update` (anything else is coerced to
  `progress`; terminal states cannot be faked);
- events must carry the parent `invoke_id` (automatic inside `bind_invoke`);
- rate limit **50 events/s per invoke**, excess dropped; host stores ≤8KB per
  event and keeps the latest 500;
- progress only flows for **async** invokes — during a plain sync
  `tools.invoke` the notifications are dropped (the tool still works).

## Token / quota notes for long jobs

- Reverse-RPC tokens (storage/image/upload/credentials/sampling) are minted
  with `min(job_timeout + 5min, 1h)` TTL and renewed automatically by the
  agent for longer jobs — nothing to do plugin-side.
- Call-count quotas scale with the job length (×hours, capped ×10).
- Per-user active job quota: **50** (`job_quota_exceeded`; per user, shared
  across apps/windows/agents, counts queued + running).
- Per-agent long-job slots: **30 running**; further jobs **queue on the
  agent** — you get a progress event `{type: "queued", data: {position}}` and
  queue time counts against `timeoutMs` — up to 100 admitted, beyond which
  `long_job_capacity` (retryable, `details.retry_after_ms`). In practice the
  per-user quota is hit first.
- Progress budget: 50 events/s per job and 200/s per user; excess is dropped
  (heartbeats, `queued` and terminal events never are).
- Concurrency is not speed: slots only guarantee a burst is never rejected.
  Heavy per-invoke resources (headless browsers, GPU) run in the agent sandbox
  (cloud: 4 vCPU / 4 GB) and are not throttled by the platform — cap them
  inside your executa.

## Concurrency burst (section 4) — reproducing the forum #474 limits

Section 4 fires *N* `invokeAsyncAwait` calls at once (own `clientTag`
`long-task-demo-burst`, so reload recovery ignores them) and tallies the
three platform layers live:

| N   | Expected                                                                 |
| --- | ------------------------------------------------------------------------ |
| 12  | all `running` immediately, no `queued` events, all succeed               |
| 40  | 30 `running` + 10 `queued` on the agent (`{type:"queued", data:{position}}`), all succeed |
| 51  | 50 created, the 51st rejected at create with `job_quota_exceeded {active:50, max_active:50, scope:"user"}` |

`long_job_capacity` (agent admitted cap 100) should never appear — the
per-user quota is hit first. Keep `steps × step_seconds` small (default
3 × 1 s) so a 40-job batch finishes in under a minute; queue time counts
against each job's `timeoutMs`.

**Plugin-side concurrency is yours to size.** The bundled engine runs each
invoke on a `ThreadPoolExecutor(max_workers=32)` so the burst exercises the
platform limits rather than the plugin. A plugin that drives headless
browsers or GPUs must cap that pool to what the sandbox can sustain —
the platform's 30 slots do not protect a 4 GB sandbox from 30 Chrome tabs.

To run a burst without the UI, open DevTools on the app iframe (the demo
exposes `window.anna`) and:

```js
const tool_id = window.__ANNA_TOOL_IDS__["long-task-engine"];
await Promise.allSettled(Array.from({ length: 40 }, () =>
  anna.tools.invokeAsync({ tool_id, method: "run_steps",
    args: { steps: 3, step_seconds: 1 }, timeoutMs: 120000, clientTag: "burst" })));
(await anna.tools.listJobs({ clientTag: "burst", state: ["queued","running"], limit: 100 })).jobs
  .reduce((m, j) => (m[j.state] = (m[j.state] || 0) + 1, m), {});
```

## Layout

```
app.json                 app-store metadata + bundled_executas handle map
manifest.json            ui.host_api.tools: ["required:bundled:long-task-engine"]
bundle/                  static-spa UI (index.html / app.js / style.css)
executas/long-task-engine-python/
  executa.json           dev tool_id + publish metadata
  long_task_engine_plugin.py   the ~200-line plugin (stdio JSON-RPC)
fixtures/happy-path.jsonl      sync fast-path fixture for `anna-app test`
```
