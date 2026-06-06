# anna-app-llm-demo

A minimal `schema: 2` Anna app that exercises the **app-side LLM &
Agent surfaces** from inside its iframe, with **two switchable paths**
to reach an LLM:

- `anna.llm.complete(...)` — direct, single-shot completion.
- `anna.tools.invoke({ tool_id, method: "complete", args })` against a
  bundled Executa (`executas/llm-via-executa-python/`) which in turn
  issues a reverse `sampling/createMessage` to the host. This lets you
  observe / shape the prompt server-side before it reaches the model.
- `anna.agent.session({ submode: "auto" })` — create a session,
  stream `run()` frames, fetch `history()`, then `delete()`.

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
completion**. Then in section 2:

1. **Create session** — mints an agent session (`submode: "auto"`).
2. **session.run** — streams tokens into the output area.
3. **session.history** — fetches the recorded transcript.
4. **session.delete** — tears down the session.

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
| `bundle/index.html` | Tiny single-page UI with mode selector |
| `bundle/app.js` | Pure DOM + `window.anna.*` calls, routes by selected mode |
| `bundle/style.css` | Light styling |
| `fixtures/happy-path.jsonl` | Mock fixtures consumed by `--mock-llm` (covers both LLM paths) |
| `executas/llm-via-executa-python/` | Sibling Executa exposing `complete` that wraps host sampling |

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

