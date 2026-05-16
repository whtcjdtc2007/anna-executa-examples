# anna-app-llm-demo

A minimal `schema: 2` Anna app that exercises the **app-side LLM &
Agent surfaces** from inside its iframe:

- `anna.llm.complete(...)` — single-shot completion.
- `anna.agent.session({ submode: "auto" })` — create a session,
  stream `run()` frames, fetch `history()`, then `delete()`.

Used as a smoke test for matrix-nexus's
`docs/design/app-llm-and-agent-access.md` Phase 6 deliverables.

---

## Run

```bash
# Mocked (offline, deterministic, no network):
pnpm dev:mock

# Against a real Nexus you've logged into:
anna-app login --host https://nexus.example.com   # one-time
pnpm dev:real

# UI-only (LLM disabled):
pnpm dev:off
```

The harness opens the bundle in a Chromium iframe. Click the buttons in
order:

1. **Run llm.complete** — calls `anna.llm.complete(...)` and dumps
   the JSON response.
2. **Create session** — mints an agent session (`submode: "auto"`).
3. **session.run** — streams tokens into the output area.
4. **session.history** — fetches the recorded transcript.
5. **session.delete** — tears down the session.

---

## Manifest grants

```json
"host_api": {
  "llm":    ["complete"],
  "chat":   ["write_message"],
  "window": ["set_title"]
}
```

> **Note on `agent.session`**: the canonical full ACL also includes
> `"agent": { "session": { "auto": true, "fixed": null }, "tools": [] }`,
> but the published `@anna-ai/app-schema@0.1.0` JSON Schema does not yet
> know about that key (it lands in 0.2.0). The host (matrix-nexus) and
> the runtime (anna-app-core) both honor it today, so once you bump the
> schema package the example manifest can grow that block — see
> `bundle/app.js` which exercises `anna.agent.session(...)` against the
> mock fixtures.

---

## Files

| Path | What |
|---|---|
| `manifest.json` | `schema: 2` manifest with `host_api.llm` + `host_api.agent.session.auto` |
| `bundle/index.html` | Tiny single-page UI |
| `bundle/app.js` | Pure DOM + `window.anna.*` calls |
| `bundle/style.css` | Light styling |
| `fixtures/happy-path.jsonl` | Mock fixtures consumed by `--llm mock` |

---

## See also

- Design spec: `matrix-nexus/docs/design/app-llm-and-agent-access.md`
- App-side API doc: `matrix-nexus/docs/developers/apps/llm-and-agent.md`
- Local dev guide: `matrix-nexus/docs/developers/apps/local-dev-llm.md`
