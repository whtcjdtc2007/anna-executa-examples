# Background Demo — a task the backend owns

The page starts a task with one `tools.invoke` and gets a task id back at
once. The bundled Executa then does the work **outside any invoke**: you can
close the window or switch to another app, and when you come back the page
shows the task's complete current state — including rows that are still
running.

The task is a **capability health check**: each row issues one family of
reverse RPCs in the background, so the table doubles as a check that every
host capability an invoke has is also available without one.

```
page ── tools.invoke(check_start)  ─▶ Executa     returns {task_id} at once
                                        │
                                        └─ runs on after the invoke returned:
                                           storage/*  files/*  web/*  sampling/*  agent/*
                                           embeddings/*  image/*  audio/*  host/uploadFile

page ── tools.invoke(check_list)   ─▶ Executa     every task's complete current state
page ── tools.invoke(check_status) ─▶ Executa     one task's complete current state
```

## Which pattern to use

| Situation | How | Who owns the task and its state |
| --- | --- | --- |
| Ask and answer | `tools.invoke`; reverse RPCs inside the invoke | — |
| **Backend-owned task** — the page only triggers; the work continues after the window closes; reopening shows the complete current state | `tools.invoke` to trigger (returns a task id), work outside any invoke, `tools.invoke` to read by id | The backend (your Executa) |
| Host-managed job — you want the host's progress stream, cancel, timeout and cleanup | `tools.invokeAsync` (see [`anna-app-long-task-demo`](../anna-app-long-task-demo)) | The host (job table) |
| Work nobody triggers — periodic sync, backup, startup checks | Background, from process start | The backend |

This demo is the second row, plus a small instance of the fourth (the
heartbeat). `tools.invokeAsync` does not replace the second row:

- On reopen the host gives you the job's status, at most 500 progress events
  of 8 KB each, and a result of at most 256 KB — what the tool reported, not
  your app's own complete state.
- The task's lifetime is tied to one invoke: it has a deadline (24 hours at
  most) and fails once the Agent has been offline for 180 seconds. The backend
  cannot pause it, resume it, or advance one task across several user steps.

## What the backend is responsible for

Owning the task means owning these, and this demo implements each:

- **Progress, cancel, timeouts and retries.** The page asks through
  `tools.invoke` (`check_status`, `check_cancel`); every check has its own
  timeout; a failed check is a table row, not the end of the task.
- **Durable state.** After every step the whole task is written to
  `scope=app` storage (`bg-check/{task_id}`). State kept only in memory is
  lost when the process restarts. After a restart, unfinished rows are
  reported as `interrupted`; nothing is re-run automatically.
- **Cloud Agent suspension.** A Cloud Agent suspends 120 minutes after its
  last invoke, and the task pauses with it. Background activity does not keep
  the machine awake. Any later invoke wakes it and the task continues; tasks
  that finish within two hours are unaffected.

## How a reverse RPC ends up in the background

The host matches every reverse RPC to an invoke through
`params.context.invoke_id`. A request that carries none — and cannot be
matched to exactly one active invoke — is served with the Executa's
**background credentials**: the same set of tokens an invoke gets, issued to
the Executa itself and available from process start.

Two lines in [`background_check_plugin.py`](executas/background-check-python/background_check_plugin.py)
decide which is which:

```python
async def _invoke(req_id, params):
    with bind_invoke(params):          # reverse RPCs here belong to this invoke
        data = await tool(**arguments)

async def _run_task(task):
    with bind_invoke(None):            # detach: everything below is background
        ...
```

`_run_task` is started from inside the `check_start` handler, so the asyncio
task inherits that invoke's binding. `bind_invoke(None)` detaches it
explicitly; the invoke is about to return.

Identity is the same in both cases: the Executa is bundled with this app, so
`scope=app` is this app's own bucket whether the call comes from an invoke or
from the background.

## What the health check covers

| Check | Reverse RPC |
| --- | --- |
| Storage KV (`scope=app`) | `storage/set` + `storage/get` |
| Object storage (`scope=app`) | `files/upload_begin` → PUT → `files/upload_complete` → `files/download_url` |
| Web search | `web/search` |
| Web fetch | `web/fetch` |
| Image search | `web/image_search` |
| Image fetch | `web/image_search` + `web/image_fetch` (tries the results in order until one downloads) |
| LLM sampling | `sampling/createMessage` |
| Agent session | `agent/session.create` + `agent/session.run` (must end with a final frame) |
| Embeddings | `embeddings/create` |
| Image generation | `image/generate` |
| Text to speech | `audio/speak` |
| Host upload | `host/uploadFile` |
| Video generation — off by default | `video/generate` |
| Music generation — off by default | `music/generate` |

**Every check spends a small amount of your CU.** Background calls are billed
like calls inside an invoke and have no cumulative cap. Video and music cost
considerably more, which is why they are off by default.

## Run it

**Locally**

```bash
npm install
npm run dev:aps      # anna-app dev --storage aps  (real account; CLI ≥ 0.1.68)
```

The local harness does not distinguish reverse RPCs inside and outside an
invoke, so the whole flow works there. With plain `npm run dev` the checks
that need a real backend fail with `not_implemented`, and the task card says
its state is not being saved — the task still runs in memory.

**On an Agent (desktop or Cloud Agent)**

```bash
anna-app apps push   # publishes the Executa, then the app
```

Install the app, open it, start a check with the wait set to 60, close the
window, and reopen it a minute later.

Background execution needs a platform and Agent that support it (see
[Executa lifecycle → Background execution](https://anna.partners/developers/tools/executa-lifecycle#background-execution)).
On an older Agent the heartbeat line reads `background unavailable` and the
rows fail with the host's `*_NOT_GRANTED` error — the same table tells you
which side is missing.

## Tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `check_start` | `items?` (check ids), `delay_s?` (0–120, default 5) | `{task_id, task}` immediately |
| `check_status` | `task_id` | `{found, task}` — the complete current state |
| `check_list` | — | `{tasks, heartbeat, checks, storage_error}` |
| `check_cancel` | `task_id` | `{cancelled, task}` |

## Tests

```bash
cd executas/background-check-python
uv run --with pytest pytest ../../tests/plugin -v
```

The test plays the host on the plugin's stdio and pins down the contract
above: reverse RPCs issued while answering an invoke carry its `invoke_id`;
the heartbeat and everything a task does after its invoke returned carry
none.

## Layout

```
anna-app-background-demo/
├── app.json                 # store metadata + bundled_executas
├── manifest.json            # schema 4; requires bundled:background-check
├── bundle/                  # index.html · app.js · style.css
├── executas/background-check-python/
│   ├── background_check_plugin.py
│   ├── executa.json
│   └── pyproject.toml
└── tests/plugin/test_background_check.py
```
