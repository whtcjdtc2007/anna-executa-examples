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
- Per-user active job quota: 5; per-agent long-job concurrency: 3
  (`long_job_capacity` error → retry later).

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
