# anna-app-session-demo

The **reference example for Anna agent sessions**: every
`agent.session` interface, over **both channels**, driven by the
**same code**.

| Channel | Path |
| --- | --- |
| **Host API** | page → `anna.agent.session.*` → host |
| **Reverse RPC** | page → `anna.tools.invoke` → bundled Executa → `agent/session.*` → host |

If you need one place to copy session code from — for an App page, for
an Executa, or for both sharing a session — this is it.

## Interfaces × channels

| Interface | Host API (page) | Reverse RPC (Executa, Python SDK) |
| --- | --- | --- |
| create | `anna.agent.session(req)` | `client.create(...)` |
| attach | `anna.agent.session.attach(uuid)` + `.refresh()` | `client.attach(uuid)` |
| list | `anna.agent.session.list(opts)` | `client.list(...)` |
| refresh | `handle.refresh()` | `client.refresh(uuid)` |
| run | `handle.run({content, run_id})` | `session.run(content, run_id=…)` |
| cancel | `handle.cancel(run_id)` | `session.cancel(run_id)` |
| history | `handle.history()` | `session.history()` |
| delete | `handle.delete()` | `session.delete()` |
| run status | `handle.runStatus(run_id)` | `client.run_status(uuid, run_id)` |
| frames replay | `anna.agent.session.frames({stream_id, after_seq})` | `client.frames(stream_id, after_seq=…)` |
| catalog | `anna.agent.session.catalog()` | `client.catalog()` |

The page renders this table itself, from the two adapters.

### One adapter per channel, no branching in the sections

`bundle/app.js` defines `hostChannel` and `executaChannel` — two objects
with the **same eleven methods**, the same arguments and the same
return shapes. The channel selector swaps one for the other; every
section only ever calls `channel.<method>()`:

```js
const info = await channel.create(req);                 // either channel
for await (const frame of channel.run(uuid, { content, run_id })) { … }
const st = await channel.run_status(uuid, run_id);
```

Two details make that possible:

- **Frames are read by structure, not by channel.** The page receives
  the OpenAI-style chunk as-is; an Executa receives it normalized
  (`{event: "delta", text}` …) with the chunk under `raw`.
  `readFrame()` understands both, so the callers never ask which
  channel they are on.
- **The app picks the `run_id` before it starts a run.** The same id
  then cancels the run and looks it up later — on either channel, even
  while the run is still in flight.

The Executa half is
`executas/session-via-executa-python/session_via_executa_plugin.py`:
**one tool**, `agent_session`, whose `op` argument selects the
interface. It uses only the public interface of
`executa_sdk.AgentSessionClient` / `AgentSession`, keeps no state (the
caller threads the uuid through, and ops that need a handle `attach`
first), and wires live frames into its stdin loop:

```python
if "method" not in msg:
    agent_client.dispatch_response(msg)          # reverse-RPC replies
elif agent_client.dispatch_notification(msg):    # live agent/session.frame
    return
```

### Same session on both channels

Inside an App, **the page and the Executas it invokes share
sessions**: a session belongs to the App that hosts the invoke, and the
Executa is only its creator. So switching the channel mid-session keeps
the session — the new channel simply `attach`es the uuid in the box and
you carry on. `list` returns the same sessions on both channels.

If the attach answers *session not found*, the page clears the session
and tells you — that is what a host without shared ownership answers
for a session the other channel created. There is no environment
probing.

The tool surface follows the hosting App too: a session created over
Reverse RPC resolves this App's declared tools (`fs_read_file` /
`fs_list_directory` / `fs_write_file`), exactly like one created by the
page. The page shows what `create` / `run_meta` actually report
(`granted_tools`, `inherit_host_tools`) and does not paper over it: on
a host without the ownership rule, a Reverse-RPC session reports the
conversation's inherited tools (`*`) instead.

### What differs between the channels

Only the delivery of frames to the page:

- **Host API** — frames reach the page one by one while the run is in
  flight.
- **Reverse RPC** — frames reach the **Executa** live
  (`agent/session.frame` notifications, Python SDK ≥ 0.4.0), but one
  `tools.invoke` answers once, so the page receives them together when
  the turn ends. The run output prints when the Executa saw its first
  frame versus the whole turn. One invoke holds at most 180 s; a longer
  turn keeps running server-side and is recovered with
  **run status → frames replay** (section 4 of the page), or started
  from an async tool job instead.

The bundled Executa is **optional** (`optional_executas`): when it is
not installed or not deployed, the page disables the Reverse RPC option
and says why — the Host API channel keeps working.

## Workspace best practices

The walkthrough exists because "the agent said it edited the file" is
one of the weakest guarantees on the platform. When cloud and local
agents coexist, workspace roots differ per machine
(`/Users/<you>/anna-workspace` vs `/data/workspace` vs
`/home/agent/...`), and a naive prompt can end with the agent writing
into the **wrong workspace** — or writing **nothing at all** while
claiming success (forum `/t/174`, `/t/86`). These practices prevent
that, on either channel:

### 1. Pin the session to one filesystem client

`submode: "fixed"` + `fixed_client_id` guarantees every run in the
session executes on **one** agent — reads and writes cannot silently
switch to a different machine's workspace mid-session:

```js
const session = await anna.agent.session({
  submode: "fixed",
  fixed_client_id: "<client-id>",
  system_prompt: PATH_DISCIPLINE, // see below
});
```

Both submodes are available to every installed App; `fixed_client_id`
must be one of the user's own registered agents.

### 2. Discover the root, then use absolute paths ONLY

Never assume the workspace root — `~` expands differently on cloud and
local agents. Step 1 of the walkthrough asks the agent to report the
**canonical absolute root** its filesystem tools actually operate on
(taken from a real tool response, not guessed from `$HOME`). Every
later instruction embeds full absolute paths under that root, and the
session `system_prompt` pins the discipline for every run:

> 1. Always call filesystem tools with ABSOLUTE paths, exactly as
>    given. Never trim a path to a relative one, never substitute a
>    root you assume is correct.
> 2. If a filesystem tool fails (e.g. `error_code
>    PATH_OUTSIDE_SANDBOX` or `NOT_FOUND`), report the structured
>    error VERBATIM and STOP. Never retry with a rewritten path and
>    never fall back to a similarly named file or directory.

The host filesystem tools honour this: absolute paths are resolved
**verbatim** (never rewritten), all tool responses return canonical
absolute paths, and failures carry a machine-readable `error_code`
plus the authorized `sandbox_root` when sandboxed.

### 3. Verify writes with a nonce — never trust completion claims

Step 2 writes a nonce to
`<root>/session-demo/proof-<nonce>.txt`, then a **separate,
independent run** reads that exact absolute path back. The **app
code** (not the model) compares the returned content against the
nonce and renders **VERIFIED / NOT VERIFIED**. This is the pattern
any production app should use before reporting "the agent updated
your file": fingerprint the expected change and check it yourself.

Related: check the **runtime-accurate tool surface** first.
`session.create` and the `run_meta` first frame of every run return
`granted_tools` / `inherit_host_tools`. If the session resolved
**zero tools** it is text-only — the walkthrough cannot touch real
files and every claimed side effect is hallucinated. Declaration is
consent: the surface is `platform registry ∩ manifest
ui.host_api.agent.tools ∩ allowed_tools` (this demo declares
`fs_read_file` / `fs_list_directory` / `fs_write_file`) — there is no
per-tool user grant to toggle. The app shows a warning banner with
that fix.

### 4. Structured errors are a stop signal, not a fallback cue

Step 3 asks the agent to read an absolute path that cannot exist in
the workspace. Correct behaviour — enforced by the session
`system_prompt` and verified in the output — is to report the tool's
structured error verbatim:

```json
{ "error_code": "PATH_OUTSIDE_SANDBOX",
  "path": "/session-demo-out-of-root-probe/probe.txt",
  "sandbox_root": "/Users/you/anna-workspace" }
```

…and **stop**. The failure modes this catches: silently trimming the
path to a relative one, guessing a different root, or "helpfully"
opening a similarly named directory (which is how historical task
workspaces get corrupted).

### 5. Classify every terminal state — and cancel for real

A run's stream ends in exactly one of these states, and the app
handles each one distinctly (forum `/t/191`):

| Frame | Meaning | App reaction |
| --- | --- | --- |
| `{event:"sse", choices:[{delta:{content}}]}` | streamed text | collect |
| `{event:"sse", choices:[{delta:{task_complete:{model, token_usage}}}]}` | success marker | show usage + the actually-routed `model` (forum `/t/270`) — proof real work was billed |
| `{event:"sse", error, error_type:"empty_completion", is_retryable:true}` | model returned **nothing** (no text, no tool call) | **retry once / switch model** — infrastructure, not a business failure |
| `{event:"sse", error, error_type:…}` | quota / recursion / provider error | surface with type |
| `{event:"error", code}` | gate error (`queue_timeout`, `session_revoked`, …) | surface with code |
| `{event:"sse", choices:[{delta:{task_cancelled}}]}` | run was cancelled | distinct outcome, not an error |
| `{event:"end"}` | terminal, always exactly once | stop reading |

The host guarantees a run can never end as an **empty success**: a
run with zero assistant output and zero tool calls is converted into
the `empty_completion` error frame server-side, so "succeeded but
nothing happened" is impossible to misread as a page/content bug.

Cancellation is real: `session.cancel(run_id)` drops a still-queued
run before it starts and stops a running one at its next checkpoint
(the stream then carries `task_cancelled` + `end`), and
`session.delete` fans the cancel out to **all** active runs of the
session — so tearing down on unmount also frees server-side workers.

### 6. Zero-frame recovery — check status, replay frames, never re-run blind

A silent stream is **not** proof the run failed: the push leg can die
(background tab, network blip) while the run keeps executing — and
billing — server-side (forum `/t/341`: 45 completed-and-charged runs,
zero frames seen, three blind retry rounds). Push pairs with pull:

- **run status** — `agent.session.run.status` (`handle.runStatus(runId)`,
  SDK ≥ 0.18.0) answers `queued | running | completed | failed |
  cancelled` for ~1h after enqueue. `completed` = the run executed and
  was billed → recover, don't re-run. `failed`/`cancelled` = re-running
  is safe.
- **replay frames** — `agent.session.frames` pages the archived
  `rpc.stream` frames by `seq` (`after_seq` cursor, `done:true` is the
  terminal). Rate limit 30/min/user, shared with the host's own
  recovery machinery — back off on `rate_limited`.

SDK ≥ 0.18.0 also arms this automatically: `run()` with no frame within
`firstFrameTimeoutMs` (default 20 s) transparently self-recovers by
polling `frames` — your `for await` loop just sees late frames. The
demo's section 4 exercises both methods manually so you can see the
decision table (`completed → replay`, `failed → re-run`) in action.

---

## Layout

```
anna-app-session-demo/
├── app.json               # listing metadata (slug: session-demo)
├── manifest.json          # schema 4 — agent tools + the optional bundled Executa
├── package.json           # dev scripts (@anna-ai/cli)
├── bundle/
│   ├── index.html         # static-spa entry
│   ├── app.js             # ES module — the two channel adapters + the sections
│   ├── anna-tool-ids.js   # generated: bundled handle → tool_id
│   └── style.css
├── executas/
│   └── session-via-executa-python/
│       ├── session_via_executa_plugin.py   # one tool: agent_session(op=…)
│       ├── executa.json
│       └── pyproject.toml
├── tests/plugin/
│   └── test_agent_session_ops.py           # op → reverse RPC, live frames, cancel
└── fixtures/
    └── happy-path.jsonl   # canned frames for offline dev (--mock-llm)
```

See `anna-app-llm-demo` for the LLM completion surfaces
(`anna.llm.complete` / `sampling/createMessage`).

## Run

First install local deps (pulls in `@anna-ai/cli`):

```bash
pnpm install
```

Then:

```bash
# Mocked (offline, deterministic, no network):
#   The fixture serves per-step canned replies (matched on prompt
#   content). No real files are touched — mock mode only smoke-tests
#   the UI flow. The default nonce (demo-nonce-0001) is what the
#   canned read-back returns, so "verify" passes; click "randomize"
#   to see the NOT VERIFIED path. Send the text EMPTY_COMPLETION_DEMO
#   in the freeform box to see empty_completion classification + retry,
#   and TASK_CANCELLED_DEMO to see a cancelled run.
#   The same fixture serves BOTH channels: on Reverse RPC the real
#   Executa runs and its agent/session.* calls are answered from it.
pnpm dev:mock

# Against a real anna server you've logged into:
#   Runs execute on your real agent; step 2 creates
#   <workspace>/session-demo/proof-<nonce>.txt on that machine.
anna-app login --host https://anna.partners   # one-time
pnpm dev:real
```

For real runs you need:

- a Matrix agent online (local, or a cloud agent), and
- the app granted **agent session** access — the declared tools
  (`fs_read_file` / `fs_list_directory` / `fs_write_file` in this
  demo's `manifest.json`) come with that single consent (declaration
  is consent; there is no per-tool toggle). An app whose manifest
  declares no `agent.tools` resolves zero tools and shows the
  `NO_TOOLS_AVAILABLE` banner instead of the walkthrough doing real
  IO.

To exercise `submode: "fixed"`, copy your agent's client id from the
dashboard (Agents page) into the `fixed_client_id` box before
creating the session.

Live frames, `attach`, `catalog` and session sharing between the two
channels need `@anna-ai/cli` ≥ 0.1.66 locally. To run the app without
the Executa (Host API only), set `"enabled": false` in
`executas/session-via-executa-python/executa.json`.

Executa tests (they play the host on the plugin's stdio):

```bash
cd executas/session-via-executa-python
uv run --with pytest --with anna-executa-test pytest ../../tests/plugin -v
```

## What to look at in the code

- `bundle/app.js` — `hostChannel` / `executaChannel` (the two
  adapters), `attachCurrent()` (what a channel switch does),
  `readFrame()` (one frame reader for both shapes),
  `DEFAULT_SYSTEM_PROMPT` (the path-discipline floor),
  `runAndCollect()` (one run + terminal-state classification), the
  three step prompts (`DISCOVER_PROMPT`, the write/verify pair,
  `PROBE_PROMPT`), and the nonce comparison in the `verify` handler.
- `executas/session-via-executa-python/session_via_executa_plugin.py`
  — `_agent_session()` (one SDK call per `op`) and `_handle_message()`
  (the stdin loop, including live frames).
- `fixtures/happy-path.jsonl` — how `--mock-llm` fixtures use
  `match.contentIncludes` to serve different canned replies to
  different `session.run` prompts.

## Cleanup

Real runs leave `<workspace>/session-demo/proof-<nonce>.txt` behind.
Delete the `<workspace>/session-demo/` directory when you're done
experimenting.
