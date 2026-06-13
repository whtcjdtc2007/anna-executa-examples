# anna-app-llm-demo

A minimal `schema: 2` Anna app that exercises the **app-side LLM &
Agent surfaces** from inside its iframe, with **two switchable paths**
to reach an LLM:

- `anna.llm.complete(...)` — direct, single-shot completion.
- `anna.tools.invoke({ tool_id, method: "complete", args })` against a
  bundled Executa (`executas/llm-via-executa-python/`) which in turn
  issues a reverse `sampling/createMessage` to the host. This lets you
  observe / shape the prompt server-side before it reaches the model.
  The Executa also exposes `method: "sample_chain"` — N sequential
  sampling calls in a single invoke — to exercise the host's
  `max_calls` quota **and** the sampling-token renewal that keeps a
  long-running invoke alive past the token's 10-minute TTL.

and the **full `agent.session` surface** — create / run / cancel /
history / **refresh** / delete / **list** — over **two switchable
transports**:

- **HOST API** — the iframe calls `anna.agent.session(...)` /
  `anna.agent.session.list(...)` directly (postMessage → host).
- **Reverse RPC** — the iframe calls `anna.tools.invoke({ method:
  "agent_session", args: { op } })` against the bundled Executa, which
  issues the matching `agent/session.*` reverse-RPC back to the host.
  Same operations, different transport — useful to compare the two.

The UI also surfaces the session's **lifecycle** (`expires_at`,
`max_lifetime_at`, `idle_ttl_seconds`) so you can watch the **sliding
idle window** slide forward on every successful call and **refresh**
renew it on demand.

It also demonstrates the **runtime-accurate tool surface** (host ≥
1.1.0-beta.45). `session.create` returns the tools the session can
*really* execute — not the manifest-declared advisory list:

- `granted_tools: ["*"]` / `inherit_host_tools: true` — the session
  inherits the user's host tools (files, browser, commands…); side
  effects like file edits are **real**.
- `granted_tools: [a, b]` — an explicit sandbox allow-list.
- `granted_tools: []` — **text-only**: the agent cannot touch local
  files, and any `changed_files` the model claims are hallucinated
  (forum `/t/86`).

Every agent run additionally opens with a **`run_meta` frame** carrying
the same fields plus a structured `warnings` list — the app checks it
for `NO_TOOLS_AVAILABLE` and shows a banner telling the user to enable
"Let agent sessions use my tools" in the app's grants drawer. Check
this **before** trusting any side effects an agent run claims to have
made.

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
completion**. Expand **Parameters** to edit the shared completion knobs —
system prompt, max tokens, temperature, stop sequences, and the MCP
`modelPreferences` (model hint + cost / speed / intelligence priority).
Blank fields are omitted from the request, so the host applies its own
defaults; whatever you set is forwarded on both paths (direct
`anna.llm.complete` args and the Executa `complete` tool args). Then in
section 2 pick a **Transport** (HOST API vs Reverse RPC) and drive the
session:

1. **create** — mints an agent session (`submode: "auto"`) and drops its
   `app_session_uuid` into the editable uuid box.
2. **run** — streams tokens into the output area.
3. **cancel** — cancels the most recent run (`run_id` tracked from the
   stream).
4. **history** — fetches the recorded transcript.
6. **refresh** — renews the session's **sliding idle window** without
   minting a new session: re-mints the short-lived capability token and
   pushes `expires_at` out to `min(now + idle_ttl, max_lifetime_at)`.
   Use it from a `visibilitychange` / focus handler, or just before a
   long idle, to keep a session alive up to its hard
   `max_lifetime_at` ceiling. The lifecycle line under the uuid box
   updates in place.
7. **delete** — tears down the session.
8. **list** — enumerates this app's active sessions. Account/app-scoped,
   so it works without an active handle (the robust way to recover
   sessions after a reload/restart). Each result is rendered as a
   clickable chip that loads its uuid into the box.

The **`app_session_uuid` box is the source of truth** for run / cancel /
history / refresh / delete: type a uuid in, or click a chip from
**list**, and the actions target *that* session — not just the one you
happened to **create** in this page load. On the HOST API transport this works via
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

## Session system prompt (set once, applies to every run)

The **Session system prompt** box (above the **create** button) is sent
as `system_prompt` at **create** time and is persisted with the session.
Every subsequent `run` in that session inherits it — you set the agent's
identity / persona / tone / output format **once** instead of repeating
it on each turn:

```js
// HOST API transport
const s = await anna.agent.session({
  submode: "auto",
  system_prompt: "You are a terse math tutor. Answer with the number only.",
});
for await (const ev of s.run({ content: "What is 12 × 13?" })) { /* 156 */ }
```

Precedence, highest to lowest:

1. **per-run `systemPrompt`** — pass it to `run({ content, systemPrompt })`
   to override the session prompt for a single turn.
2. **session `system_prompt`** — the value set here at create time.
3. host default persona — used when neither is supplied.

The platform **safety floor is always prepended and cannot be
overridden** by either value. The prompt is capped at **4000 characters**
and is treated as untrusted input (a few control tokens are rejected at
create time). Leaving the box empty creates a session with no custom
prompt, exactly as before. The Reverse RPC transport threads the same
value through `agent_session(op: "create", system_prompt: …)`.

---


## Session lifecycle (sliding idle window)

An agent session carries **two clocks**, both surfaced in the UI's
`lifecycle:` line:

| Field | Meaning |
|---|---|
| `expires_at` | Idle deadline. Slides to `min(now + idle_ttl_seconds, max_lifetime_at)` on **every** successful op (create / run / history / refresh). |
| `max_lifetime_at` | Hard ceiling, fixed at create time. The idle window can never slide past it. |
| `idle_ttl_seconds` | Size of the idle window that each successful call re-arms. |

This is intentionally decoupled from the short-lived **capability
token** (`session_expires_in`, minutes) that authorizes the postMessage
/ reverse-RPC calls: the token can expire while the *session* is still
alive, and the next call (or an explicit **refresh**) silently re-mints
it. Two recovery patterns the demo demonstrates:

- **Token-free release** — `delete` / `list` are keyed by
  `(account, app, uuid)`, not by the token, so they work even after the
  token has lapsed (e.g. after a long background tab).
- **Resume-by-uuid** — `refresh(uuid)` re-mints the token *and* slides
  the idle window from just the uuid, the canonical way to revive a
  session after an iframe reload without re-`create`-ing.

```js
// Keep a session warm across a tab going background → foreground:
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && handle) {
    const info = await handle.refresh();        // host transport
    // info.expires_at / info.max_lifetime_at now refreshed
  }
});
```

On the **Reverse RPC** transport the same renewal flows through the
Executa: `anna.tools.invoke({ method: "agent_session", args: { op:
"refresh", app_session_uuid } })`, which issues an
`agent/session.refresh` reverse-RPC; the host re-mints the per-executa
sampling-scoped token and returns the fresh lifecycle.

When a session is past its deadline the call rejects with a stable
`error.name` (the demo's error box hints on these):

| `error.name` | Meaning | Recover by |
|---|---|---|
| `APP_SESSION_TOKEN_EXPIRED` | Token lapsed, session still alive | `refresh(uuid)` (or any op auto-remints) |
| `APP_SESSION_EXPIRED` | Idle/hard deadline passed | `create` a new session |
| `APP_SESSION_REVOKED` | Explicitly revoked / deleted | `create` a new session |

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
| `bundle/index.html` | Single-page UI: LLM source selector + session transport selector + lifecycle line |
| `bundle/app.js` | Pure DOM + `window.anna.*` calls; routes by selected mode + transport; renders lifecycle + `error.name` hints |
| `bundle/style.css` | Light styling |
| `fixtures/happy-path.jsonl` | Mock fixtures consumed by `--mock-llm` (both LLM paths + all `agent.session.*` ops incl. `list` + `refresh`) |
| `executas/llm-via-executa-python/` | Sibling Executa exposing `complete` (sampling) + `agent_session` (reverse-RPC `agent/session.*`, incl. `refresh`) |

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

