# anna-app-embed-demo

A minimal `schema: 2` Anna app that exercises the **host-managed
Embeddings** surface from inside its iframe, with **two switchable
paths**:

- `anna.llm.embed(...)` — direct HOST API call from the iframe.
- `anna.tools.invoke({ tool_id, method: "embed", args })` against a
  bundled Executa (`executas/embed-via-executa-python/`) which in turn
  issues a reverse-RPC `embeddings/create` to the host. Useful for
  observing / pre/post-processing the embedding server-side.

Used as the canonical smoke test for `matrix-nexus`'
[`docs/design/app-llm-embeddings.md`](../../../matrix-nexus/docs/design/app-llm-embeddings.md)
and the Executa v2 embeddings surface
(`executa-sdk.EmbeddingsClient`).

> Billing model (2026-05-28): embed calls are billed per token via
> `LLMTokenUsageService` (`request_type=APP_EMBED` /
> `EXECUTA_EMBED`). No free quota pool, no per-call cap — pure
> pay-per-token. See §17 of the design doc.

---

## Run

```bash
pnpm install
```

Then:

```bash
# Mocked (offline, deterministic, no network):
#   - "Direct" path uses the fixture's `llm.embed` entry.
#   - "Via Executa" path uses the fixture's `tools.invoke` entry —
#     the Executa subprocess does NOT actually call embeddings/create
#     in mock mode (the bridge intercepts the tools.invoke call).
pnpm dev:mock

# Against a real anna server you've logged into:
#   - Both embed paths reach a real provider. The Executa subprocess
#     is spawned and uses embeddings/create to round-trip via host.
anna-app login --host https://anna.partners   # one-time
pnpm dev:real

# UI-only (LLM disabled):
pnpm dev:off
```

The harness opens the bundle in a Chromium iframe. Use the
**Embed source** selector to switch paths before clicking **Compute
embedding**. The UI displays:

- the resolved model alias + dimensions
- the first 8 floats of the returned vector
- `usage.prompt_tokens`, latency, and (in real mode) `_meta.costUsd`

---

## Manifest grants

```json
"permissions": ["chat.write_message", "tools.invoke"],
"required_executas": [
  { "tool_id": "tool-test-embed-via-executa-12345678", "min_version": "0.1.0", "version": "latest" }
],
"host_api": {
  "llm":    ["embed"],
  "tools":  ["required:tool-test-embed-via-executa-12345678"],
  "chat":   ["write_message"],
  "window": ["set_title"]
}
```

The bundled Executa declares `host_capabilities: ["llm.embed"]` in
its `MANIFEST` — without it the host refuses the reverse-RPC with
`-32501 EMBED_NOT_GRANTED`.

---

## Files

| Path | What |
|---|---|
| `manifest.json` | `schema: 2` manifest with `host_api.llm: ["embed"]` |
| `bundle/index.html` | Single-page UI with mode selector + result panel |
| `bundle/app.js` | Pure DOM + `window.anna.*` calls, routes by selected mode |
| `bundle/style.css` | Light styling (cloned from `anna-app-llm-demo`) |
| `fixtures/happy-path.jsonl` | Mock fixtures consumed by `--mock-llm` (covers both embed paths) |
| `executas/embed-via-executa-python/` | Sibling Executa exposing `embed` that wraps host embeddings/create |

---

## See also

In the anna server repo (project codename: `matrix-nexus`):

- Design spec: `docs/design/app-llm-embeddings.md`
- Companion example: `examples/anna-app-llm-demo/` — same shape, but
  exercises `anna.llm.complete` + reverse `sampling/createMessage`.
