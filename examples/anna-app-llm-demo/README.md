# anna-app-llm-demo

A minimal `schema: 2` Anna app that exercises the **app-side LLM &
Agent surfaces** from inside its iframe, with **two switchable paths**
to reach an LLM:

- `anna.llm.complete(...)` — direct, single-shot completion.
- `anna.tools.invoke({ tool_id, method: "complete", args })` against a
  bundled Executa (`executas/llm-via-executa-python/`) which in turn
  issues a reverse `sampling/createMessage` to the host. This lets you
  observe / shape the prompt server-side before it reaches the model.

and the **full `agent.session` surface** — create / run / cancel /
history / delete / **list** — over **two switchable transports**:

- **HOST API** — the iframe calls `anna.agent.session(...)` /
  `anna.agent.session.list(...)` directly (postMessage → host).
- **Reverse RPC** — the iframe calls `anna.tools.invoke({ method:
  "agent_session", args: { op } })` against the bundled Executa, which
  issues the matching `agent/session.*` reverse-RPC back to the host.
  Same operations, different transport — useful to compare the two.

Used as a smoke test for the **anna server**'s
`docs/design/app-llm-and-agent-access.md` Phase 6 deliverables and the
Executa v2 sampling surface.

---

## Run

First install local deps (pulls in `@anna-ai/cli`):

```bash
pnpm install
```

Then:

```bash
# Mocked (offline, deterministic, no network):
#   - "Direct" path uses the fixture's `llm.complete` entry.
#   - "Via Executa" path uses the fixture's `tools.invoke` entry —
#     the Executa subprocess does NOT actually run sampling in mock
#     mode (the bridge intercepts the tools.invoke call).
pnpm dev:mock

# Against a real anna server you've logged into:
#   - Both LLM paths reach a real model. The Executa subprocess is
#     spawned and uses sampling/createMessage to round-trip via host.
anna-app login --host https://anna.partners   # one-time
pnpm dev:real

# UI-only (LLM disabled):
pnpm dev:off
```

The harness opens the bundle in a Chromium iframe. In section 1 use the
**LLM source** selector to switch paths before clicking **Run
completion**. Then in section 2 pick a **Transport** (HOST API vs
Reverse RPC) and drive the session:

1. **create** — mints an agent session (`submode: "auto"`) and drops its
   `app_session_uuid` into the editable uuid box.
2. **run** — streams tokens into the output area.
3. **cancel** — cancels the most recent run (`run_id` tracked from the
   stream).
4. **history** — fetches the recorded transcript.
5. **delete** — tears down the session.
6. **list** — enumerates this app's active sessions. Account/app-scoped,
   so it works without an active handle (the robust way to recover
   sessions after a reload/restart). Each result is rendered as a
   clickable chip that loads its uuid into the box.

The **`app_session_uuid` box is the source of truth** for run / cancel /
history / delete: type a uuid in, or click a chip from **list**, and the
actions target *that* session — not just the one you happened to
**create** in this page load. On the HOST API transport this works via
`anna.agent.session.attach(uuid)`, a client-side helper (SDK ≥ 0.7.0)
that re-binds an existing session as an `AgentSession` handle **without**
minting a new one (no RPC), giving you the same streaming `.run()` /
`.cancel()` / `.history()` / `.delete()` sugar. This is exactly how a
real app re-attaches to a session after an iframe reload, a crash, or
from another tab:

```js
const { sessions } = await anna.agent.session.list({ limit: 50 });
const handle = anna.agent.session.attach(sessions[0]); // uuid string or list() row
for await (const ev of handle.run({ content: "continue" })) { /* … */ }
```

Switching the transport selector clears the uuid box and current handle
(host vs executa sessions are scoped differently), so create or pick a
fresh session after toggling.

> In **mock** mode the `agent.session.*` operations are served from
> `fixtures/happy-path.jsonl` for *both* transports (the Reverse RPC
> path routes the Executa's `agent/session.*` reverse-RPC through the
> same bridge). `list` returns the fixture's canned session array; in
> **real** mode it returns this run's live sessions.

---

## Manifest grants

```json
"permissions": ["chat.write_message", "tools.invoke"],
"required_executas": [
  { "tool_id": "bundled:llm-via-executa", "min_version": "0.1.0", "version": "latest" }
],
"host_api": {
  "llm":    ["complete"],
  "chat":   ["write_message"],
  "window": ["set_title"]
}
```

> **`host_api.tools` is optional.** When omitted (or `[]`) the window may
> invoke **any** tool_id declared in `required_executas` / `optional_executas`.
> List explicit refs only to *narrow* a window to a subset; prefer
> `["required:*"]` over a concrete id, since `bundled:` handles are rewritten
> to the server-minted tool_id at `dev`/`publish` time and a pinned id drifts.
>
> The bundle resolves the concrete id at runtime from
> `window.__ANNA_TOOL_IDS__["llm-via-executa"]` (set by the generated
> `anna-tool-ids.js`), so it never hardcodes the minted id.

> **Note on `agent.session`**: the canonical full ACL also includes
> `"agent": { "session": { "auto": true, "fixed": null }, "tools": [] }`,
> but the published `@anna-ai/app-schema@0.1.0` JSON Schema does not yet
> know about that key (it lands in 0.2.0). The anna server and the
> runtime (anna-app-core) both honor it today, so once you bump the
> schema package the example manifest can grow that block — see
> `bundle/app.js` which exercises `anna.agent.session(...)` against the
> mock fixtures.

---

## Files

| Path | What |
|---|---|
| `manifest.json` | `schema: 2` manifest with `host_api.llm` + `host_api.tools` + `host_api.agent.session.auto` |
| `bundle/index.html` | Single-page UI: LLM source selector + session transport selector |
| `bundle/app.js` | Pure DOM + `window.anna.*` calls; routes by selected mode + transport |
| `bundle/style.css` | Light styling |
| `fixtures/happy-path.jsonl` | Mock fixtures consumed by `--mock-llm` (both LLM paths + all `agent.session.*` ops incl. `list`) |
| `executas/llm-via-executa-python/` | Sibling Executa exposing `complete` (sampling) + `agent_session` (reverse-RPC `agent/session.*`) |

---

## See also

In the anna server repo (project codename: `matrix-nexus`):

- Design spec: `docs/design/app-llm-and-agent-access.md`
- App-side API doc: `docs/developers/apps/llm-and-agent.md`
- Local dev guide: `docs/developers/apps/local-dev-llm.md`

In this repo:

- `examples/python/sampling-summarizer/` — fuller example of the
  reverse-sampling pattern this app's Executa is modeled after.
- `examples/anna-app-focus-flow/` — fuller example of an app bundled
  with sibling Executas under `executas/`.

