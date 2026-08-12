# Visual Brand — Anna App sample

A focused canvas that demonstrates the **app-side** half of the
`executa-llm-image-generation` RFC v2:

| UI action       | HTTP endpoint                                  |
| --------------- | ---------------------------------------------- |
| **Generate**    | `POST /api/v1/copilot/app/image/generate`      |
| **Restyle**     | `POST /api/v1/copilot/app/image/edit`          |
| **Persist**     | `POST /api/v1/copilot/app/upload` (inline)     |

Auth is the `app_session_token` minted by the AnnaApp runtime during
handshake — the app holds zero long-lived credentials and never touches
an LLM API key.

## Advanced image options (v0.14+)

`anna.image.generate` / `anna.image.edit` accept optional
provider-specific knobs. All are validated host-side; **models that
don't support an option silently ignore it and are never surcharged
for it**:

| Option              | Values                          | Applies to             | Billing impact                         |
| ------------------- | ------------------------------- | ---------------------- | -------------------------------------- |
| `quality`           | `low` \| `medium` \| `high`     | GPT-Image family       | drives per-image price (~$0.006–$0.40) |
| `resolution`        | `0.5K` \| `1K` \| `2K` \| `4K`  | Nano-Banana family     | 0.75× / 1× / 1.5× / 2× base rate       |
| `output_format`     | `png` \| `jpeg` \| `webp`       | fal-hosted models      | none                                   |
| `enable_web_search` | `boolean`                       | Nano Banana 2 generate | small per-call surcharge               |
| `thinking_level`    | `minimal` \| `high`             | Nano Banana 2 generate | `high` adds a small surcharge          |
| `mask_url` (edit)   | 1-channel PNG URL               | mask-capable models (GPT Image 2) | none; unsupported → `-32312` |

```js
const out = await anna.image.generate({
  prompt: "A minimalist poster about Mars",
  size: "1024x1536",
  quality: "medium",        // GPT-Image models
  resolution: "2K",         // Nano-Banana models
  output_format: "png",
});
```

The UI exposes **Quality** and **Resolution** selectors so you can watch
`quota_used` change with the pricing tier.

## Model preference (request-level)

Both paths accept an MCP-shaped model hint. The host resolves it against
active image models — **plan-checked**, capability-checked
(`text-to-image` / `image-editing`) — and **silently falls back** to the
user's `image_gen` / `image_edit` preference when the hint misses (never
an error):

```js
await anna.image.generate({
  prompt: "...",
  modelPreferences: { hints: [{ name: "fal-ai/nano-banana-2" }] },
});
```

The **Model** selector drives this on both call paths; the actually-used
model is always reported back in `out.model`.

## Call-path switch: Host API vs Reverse RPC

The **Call path** selector demonstrates the two ways an image reaches
the same Nexus gate:

| Path            | Route                                                                    |
| --------------- | ------------------------------------------------------------------------ |
| **Host API**    | iframe → `anna.image.generate` postMessage RPC → `app_llm_facade`         |
| **Reverse RPC** | iframe → `anna.tools.invoke("image_poster", …)` → executa plugin → plugin SDK `image/generate` → Matrix Agent → Nexus image gate |

The Reverse RPC path drives the **bundled** copy of the image-poster
plugin at [`executas/image-poster/`](executas/image-poster/) (same code
as the standalone sample
[`examples/python/image-poster/`](../python/image-poster/)), whose
`poster_create` / `poster_restyle` tools forward the `model` argument as
wire `modelPreferences`.

**No manual registration needed.** The manifest references it as
`bundled:image-poster` (declared in `app.json#bundled_executas`), so
`anna-app apps push` / `publish` automatically publishes the executa
first, substitutes the minted `tool_id` into the manifest, and writes
`bundle/anna-tool-ids.js` (`window.__ANNA_TOOL_IDS__`) which `app.js`
reads at runtime. During local `anna-app dev` the sidecar is absent and
`app.js` falls back to the dev placeholder id `tool-dev-image-poster`
from `executas/image-poster/executa.json`.

The user must still install/enable the executa and toggle its
`image_grant` in the Anna Admin panel; if it's missing the UI surfaces
the invoke error and the Host API path keeps working.

## Install + run

```bash
pnpm install
pnpm dev                       # anna-app dev — opens the iframe + harness
```

In standalone preview (`anna-app dev` without a live Nexus account) the
fetch calls go to `window.location.origin`, which the harness mocks.
For end-to-end testing point at a real Nexus:

```bash
anna-app login --host https://nexus.example.com
pnpm dev
```

## Permissions / grants

The end-user must have toggled the matching grant in their Anna Admin
panel for this app:

- `image_grant.generate = true`           → enables **Generate**
- `image_grant.edit = true`               → enables **Restyle**
- `upload_grant.enabled = true`           → enables **Persist**

There is no per-grant MIME whitelist — uploads only need to clear the
host's hard denylist (executables, `image/svg+xml`); `image/png` always
passes.

Without the grant, the host returns HTTP 403 with body
`{"code":-32101,"message":"image_not_granted"}` (or `-32201`); the app
surfaces this verbatim in the status line.

## See also

- Host RFC: [matrix-nexus/docs/design/executa-llm-image-generation.md](../../../matrix-nexus/docs/design/executa-llm-image-generation.md)
- Plugin-side sample (reverse-RPC): [anna-executa-examples/examples/python/image-poster/](../../../anna-executa-examples/examples/python/image-poster/)
- Fixtures: [`fixtures/happy-path.jsonl`](fixtures/happy-path.jsonl)
