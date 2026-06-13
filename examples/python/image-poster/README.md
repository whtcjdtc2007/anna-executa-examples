# image-poster — Executa v2 image plugin example

A reference plugin that exercises **three** Executa v2 reverse-RPCs:

| Tool             | Reverse-RPC used   | Error namespace |
| ---------------- | ------------------ | --------------- |
| `poster_create`  | `image/generate`   | `-32101..-32110` |
| `poster_restyle` | `image/edit`       | `-32311..-32314` (+ image errors) |
| `poster_persist` | `host/uploadFile`  | `-32201..-32213` |

The host (Nexus) owns provider selection, billing, quota, and storage —
the plugin never holds a model API key, never holds S3 credentials, and
is fully offline-testable via `anna-app-cli`.

## Install

```bash
cd anna-executa-examples/examples/python/image-poster
uv sync                       # installs executa-sdk via local path source
```

## A. Offline (mock host, no PAT, no network)

The bridges in `anna-app-cli` synthesise deterministic responses if the
fixture has no matching entry, so even an empty fixture file lets the
tool complete a happy-path round-trip.

### A.1 Generate (matches `Mars` rule in `image.jsonl`)

```bash
anna-app executa dev --dir .                                       \
    --mock-image fixtures/image.jsonl                              \
    --mock-upload fixtures/upload.jsonl                            \
    --invoke poster_create                                         \
    --args '{"topic": "the planet Mars", "style": "art-deco"}'
```

Expect `images[0].url == https://mock.cdn/mars-poster.png` (fixture row 1).

### A.2 Generate (fallback row)

```bash
anna-app executa dev --dir .                                       \
    --mock-image fixtures/image.jsonl --mock-upload fixtures/upload.jsonl \
    --invoke poster_create                                         \
    --args '{"topic": "a quiet harbour at dawn"}'
```

Expect `images[0].url == https://mock.cdn/default-poster.png` (fixture row 2).

### A.3 Restyle

```bash
anna-app executa dev --dir .                                       \
    --mock-image fixtures/image.jsonl --mock-upload fixtures/upload.jsonl \
    --invoke poster_restyle                                        \
    --args '{"image_url": "https://mock.cdn/mars-poster.png", "style": "cyberpunk"}'
```

Expect `images[0].url == https://mock.cdn/restyled-poster.png`.

### A.4 Persist (inline upload)

```bash
anna-app executa dev --dir .                                       \
    --mock-image fixtures/image.jsonl --mock-upload fixtures/upload.jsonl \
    --invoke poster_persist                                        \
    --args '{"image_url": "https://httpbin.org/image/png"}'
```

> `--mock-upload` mode still has the plugin perform the source download
> (so URL-error paths exercise correctly), then the mock bridge
> fabricates a `data:` URL response. Any reachable PNG works;
> `httpbin.org/image/png` is the recommended test URL.

### A.5 REPL mode (interactive)

```bash
anna-app executa dev --dir . --mock-image fixtures/image.jsonl --mock-upload fixtures/upload.jsonl
# > describe
# > invoke poster_create {"topic":"Saturn"}
# > invoke poster_restyle {"image_url":"https://mock.cdn/mars-poster.png","style":"watercolor"}
# > quit
```

## B. Real Nexus — **auto-registration mode (default)**

Since `anna-app-cli ≥ 0.1.15`, `executa dev` registers the current
plugin as a **dev Executa** on first run (mirrors how `anna-app dev`
auto-registers anna-apps). No `--app-slug` required, no manual
`anna-app executa register` step.

```bash
anna-app login --host https://nexus.example.com
anna-app executa dev --dir .                                       \
    --invoke poster_create                                         \
    --args '{"topic": "the planet Mars", "style": "art-deco"}'
```

You will see, in the banner:

```
executa   auto-registered as executa-image-poster
sampling  real → app_slug=executa-image-poster (auto)
image     real → app_slug=executa-image-poster (auto)
upload    real → app_slug=executa-image-poster (auto)
```

The CLI POSTs to `/api/v1/anna-apps/dev/executas/register` (PAT-authed),
nexus upserts an `AnnaApp(kind="executa", is_dev=True, status=DRAFT)`
and seeds three dev grants on the developer's own user (`llm_grant`,
`image_grant`, `upload_grant`) with generous quotas suitable for
local iteration. The result is cached at `.anna/dev-executa.json`
(cache_version=2) — delete that file to force a refresh, e.g. after
toggling grants in the Hub.

### B.1 End-to-end happy path

```bash
# Generate → restyle → persist, three independent invokes
anna-app executa dev --dir . --invoke poster_create               \
    --args '{"topic":"a neon-lit Tokyo back-alley","style":"cyberpunk","size":"1024x1024"}'

anna-app executa dev --dir . --invoke poster_restyle              \
    --args '{"image_url":"<url-from-step-1>","style":"watercolor"}'

anna-app executa dev --dir . --invoke poster_persist              \
    --args '{"image_url":"https://httpbin.org/image/png","filename":"poster.png"}'
```

### B.2 Error matrix (recommended to walk through once)

| Test                              | Args                                                                      | Expected outcome             |
| --------------------------------- | ------------------------------------------------------------------------- | ---------------------------- |
| Empty topic                       | `{"topic":"  "}` on `poster_create`                                       | `{"images":[],"note":"empty topic"}` (graceful, no RPC) |
| Bad URL scheme                    | `{"image_url":"ftp://example.com/x.png"}` on `poster_persist`             | `ValueError: unsupported URL scheme: ftp` |
| 404 source                        | `{"image_url":"https://httpbin.org/status/404"}` on `poster_persist`      | `urllib.error.HTTPError: HTTP 404` |
| Oversized payload (>8 MB inline)  | URL pointing at a file > 8 MB                                             | `{"ok":false,"error":"image exceeds 8 MB inline cap; ..."}` |
| Invalid `purpose`                 | edit `image_poster.py` to use `purpose="image"` then `poster_persist`     | `[-32003] APP_PROVIDER_ERROR: purpose 'image' not in protocol whitelist` |
| Grant disabled (Hub)              | toggle `image_grant.allowGenerate=false` then `poster_create`             | `[-32101] IMAGE_NOT_GRANTED` |
| Capability not declared           | edit `MANIFEST` to drop `"host.upload"` then `poster_persist`             | `[-32210] NOT_NEGOTIATED` |

### B.3 Pinning to a specific app slug (opt-out of auto-registration)

If you want this Executa to bill against a **regular** AnnaApp you
already own (e.g. to share grants with browser-side code), pass
`--app-slug` explicitly — auto-registration is skipped:

```bash
anna-app executa dev --dir . --app-slug my-poster-app              \
    --invoke poster_persist                                        \
    --args '{"image_url":"https://httpbin.org/image/png"}'
```

The slug must already exist (`anna-app dev` once in the corresponding
app project to register it).

### B.4 One-shot JSON output (script-friendly)

```bash
anna-app executa dev --dir . --json --invoke poster_create        \
    --args '{"topic":"Mars"}' | jq '.images[0].url'
```

Banners are suppressed in `--json` mode so the tool output is the
**only** stdout line.

## Manifest

```json
{
  "host_capabilities": ["llm.image", "llm.image.edit", "host.upload"]
}
```

Omit any capability you do not need — Nexus rejects un-negotiated
reverse-RPCs with `NOT_NEGOTIATED` (`-32107` / `-32210`).

## Upload `purpose` whitelist

`host/uploadFile` constrains `purpose` to the protocol whitelist:

| Purpose            | Use case                                          |
| ------------------ | ------------------------------------------------- |
| `image_input`      | Pass a user-supplied image into a model call.     |
| `image_reference`  | Style/composition reference image.                |
| `user_artifact`    | Final asset for the user to keep (this example).  |

`poster_persist` uses `user_artifact`. Any other value fails with
`[-32003] APP_PROVIDER_ERROR: purpose '...' not in protocol whitelist`.

## See also

- SDK reference: `anna-executa-examples/sdk/python/executa_sdk/image.py`
- Host RFC: `matrix-nexus/docs/design/executa-llm-image-generation.md`
- App-side companion: `anna-executa-examples/examples/anna-app-visual-brand/` (browser-side HTTP).
