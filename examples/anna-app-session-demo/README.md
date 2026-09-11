# anna-app-session-demo

A minimal `schema: 2` Anna app that demonstrates **Agent Session
workspace best practices** — how to drive `anna.agent.session.*` when
the agent is expected to **read and write workspace files reliably**.

It exists because "the agent said it edited the file" is one of the
weakest guarantees on the platform. When cloud and local agents
coexist, workspace roots differ per machine
(`/Users/<you>/anna-workspace` vs `/data/workspace` vs
`/home/agent/...`), and a naive prompt can end with the agent writing
into the **wrong workspace** — or writing **nothing at all** while
claiming success (forum `/t/174`, `/t/86`).

The app demonstrates the five practices that prevent this:

## 1. Pin the session to one filesystem client

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

Requires the manifest grant (this app declares both submodes):

```json
"agent": { "session": { "auto": true, "fixed": {} }, "tools": [] }
```

`"fixed": {}` allows any client id; list explicit ids in
`"fixed": { "client_ids": ["…"] }` to allow-list.

## 2. Discover the root, then use absolute paths ONLY

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

## 3. Verify writes with a nonce — never trust completion claims

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
files and every claimed side effect is hallucinated. The app shows a
warning banner and the fix (enable "Let agent sessions use my tools"
in the app's grants drawer).

## 4. Structured errors are a stop signal, not a fallback cue

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

## 5. Classify every terminal state — and cancel for real

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

---

## Layout

```
anna-app-session-demo/
├── app.json               # listing metadata (slug: session-demo)
├── manifest.json          # schema 2 — agent.session {auto, fixed} grant
├── package.json           # dev scripts (@anna-ai/cli)
├── bundle/
│   ├── index.html         # static-spa entry
│   ├── app.js             # ES module — session setup + 3-step walkthrough
│   └── style.css
└── fixtures/
    └── happy-path.jsonl   # canned frames for offline dev (--mock-llm)
```

No bundled Executas — this demo is pure HOST API
(`anna.agent.session.*` from the iframe). See `anna-app-llm-demo` for
the Reverse-RPC transport (`tools.invoke` → `agent/session.*`) and the
LLM completion surfaces.

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
#   in the freeform box to see empty_completion classification + retry.
pnpm dev:mock

# Against a real anna server you've logged into:
#   Runs execute on your real agent; step 2 creates
#   <workspace>/session-demo/proof-<nonce>.txt on that machine.
anna-app login --host https://anna.partners   # one-time
pnpm dev:real
```

For real runs you need:

- a Matrix agent online (local, or a cloud agent),
- the app granted **agent session** access, and
- "Let agent sessions use my tools" enabled in the app's grants
  drawer — otherwise the session resolves zero tools and the app
  shows the `NO_TOOLS_AVAILABLE` banner instead of the walkthrough
  doing real IO.

To exercise `submode: "fixed"`, copy your agent's client id from the
dashboard (Agents page) into the `fixed_client_id` box before
creating the session.

## What to look at in the code

- `bundle/app.js` — `DEFAULT_SYSTEM_PROMPT` (the path-discipline
  floor), `runAndCollect()` (streamed run + `run_meta` handling),
  the three step prompts (`DISCOVER_PROMPT`, the write/verify pair,
  `PROBE_PROMPT`), and the nonce comparison in the `verify` handler.
- `fixtures/happy-path.jsonl` — how `--mock-llm` fixtures use
  `match.contentIncludes` to serve different canned replies to
  different `session.run` prompts.

## Cleanup

Real runs leave `<workspace>/session-demo/proof-<nonce>.txt` behind.
Delete the `<workspace>/session-demo/` directory when you're done
experimenting.
