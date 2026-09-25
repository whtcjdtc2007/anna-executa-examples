# anna-app-web-search-demo

A `schema: 3` Anna App (display name **Web Search Demo**) that demonstrates
Anna's host-managed **web capability** over **both channels** — the iframe
**HOST API** (`anna.web.search` / `anna.web.fetch` / `anna.web.image_search`
/ `anna.web.image_fetch`) and the **Reverse RPC** twin (`web/search` /
`web/fetch` / `web/image_search` issued by a bundled Executa).

> Reference:
> [Host API · web.*](https://anna.partners/developers/reference) and the
> design doc `matrix-nexus/docs/design/app-web-search.md`.

```
HOST API (no executa involved):
  iframe ── anna.web.search({query,...}) ──▶ host web facade ─▶ provider chain (tavily → ddgs)
  iframe ── anna.web.fetch({urls:[...]}) ──▶ host SSRF-guarded fetcher ─▶ Markdown
  iframe ── anna.web.image_search({query}) ▶ host ─▶ ddgs.images → tavily (safe-search forced)
  iframe ── anna.web.image_fetch({url}) ──▶ host download ─▶ APS files artifact (path + get_url)

Reverse RPC (bundled Executa):
  iframe ── tools.invoke(web_search) ───▶ Executa ── web/search ────▶ host ─▶ provider chain
  iframe ── tools.invoke(web_research) ─▶ Executa ── web/search + web/fetch ─▶ host
  iframe ── tools.invoke(web_image_search) ▶ Executa ── web/image_search ─▶ host
  iframe ── tools.invoke(web_image_grab) ─▶ Executa ── web/image_fetch ─▶ host ─▶ APS artifact
```

## Why route web access through the host

Your code — bundle **or** plugin — never holds a search-provider API key.
Provider routing (tavily → ddgs degrade chain), SSRF guarding, quota,
billing and audit all live host-side, and the response schema is
provider-agnostic. `search_depth` expresses a *quality intent*, never a
provider choice: the response reports the **actually-executed**
`provider_tier` and a degraded `advanced` request bills the cheaper rate.

## Two channels, one wire contract

| | HOST API (iframe) | Reverse RPC (executa) |
| --- | --- | --- |
| Call | `anna.web.search(...)` over postMessage | `web/search` JSON-RPC server request |
| Declared by | `manifest.ui.host_api.web: ["search", "fetch", "image_search", "image_fetch"]` | `host_capabilities: ["web.search", "web.fetch", "web.image_search", "web.image_fetch"]` |
| User consent | per-app `web_grant` | per-executa `web_grant` |
| fetch budget | 20,000 chars/page · 512 KB/response | **8,000 chars/page · 256 KB/response** (stdio) |
| Ledger | `APP_WEB_SEARCH` / `APP_WEB_FETCH` / `APP_WEB_IMAGE_*` | `EXECUTA_WEB_SEARCH` / `EXECUTA_WEB_FETCH` / `EXECUTA_WEB_IMAGE_*` |

Parameters, response envelope and **pricing are identical** (basic search
≈ 0.5 CU floor; fetch = 1.0 CU/URL) — the demo renders both search results
side by side so you can verify the contract never forks, and neither
channel is a billing back door.

## Tools exposed by the Executa

| Tool               | Does                                                                                   | Returns |
| ------------------ | -------------------------------------------------------------------------------------- | ------- |
| `web_search`       | One reverse `web/search` round-trip.                                                   | `{ ok, channel, results, provider_tier, quota_consumed }` |
| `web_research`     | Minimal research pipeline: `web/search`, then `web/fetch` the top N pages (Markdown).  | `{ ok, channel, query, results, pages, quota_consumed }` |
| `web_image_search` | Image search (Phase 2) — safe-search force-enabled host-side.                          | `{ ok, channel, results, quota_consumed, cached, _meta? }` |
| `web_image_grab`   | Image download (Phase 2) → APS files artifact; returns a reference, **never bytes**.   | `{ ok, channel, path, get_url, mime_type, bytes_size, sha256, … }` |

The Executa declares `host_capabilities: ["web.search", "web.fetch",
"web.image_search", "web.image_fetch"]`.
Without them the host refuses with `WEB_NOT_GRANTED` (-32521); other codes
you'll meet: `-32522` (CU quota exhausted), `-32523` (provider chain down),
`-32528` (sampling token expired). The image methods additionally need the
user's `web_grant.allowImageSearch` / `allowImageFetch`, which **default
OFF** in production (the dev install seeds them on).

## Per-item failure isolation (fetch)

`web.fetch` / `web/fetch` take 1–10 URLs and return `pages[]` in the same
order — one blocked or broken page **never fails the batch**. Check
`pages[i].ok`; error values include `SSRF_BLOCKED`, `TIMEOUT`,
`HTTP_<status>`, `PARSE_FAILED`, `TOO_LARGE`, `RESPONSE_BUDGET_EXCEEDED`.
Paste `http://169.254.169.254/` into the demo's fetch box to watch the
SSRF guard (private/metadata IPs blocked after DNS resolution, re-validated
per redirect hop) answer `SSRF_BLOCKED` while the call itself succeeds.

## Run it

```bash
cd examples/anna-app-web-search-demo
pnpm install         # or npm install — pulls @anna-ai/cli
pnpm dev             # anna-app dev, signed in
```

- **Signed in**: the dev install seeds a fully-enabled `web_grant` on both
  the app and the bundled Executa — no manual permission toggling, and dev
  calls are **never billed**. The harness runs the HOST API channel with a
  keyless local implementation (ddgs + stdlib SSRF fetcher), so search
  works without any provider key.
- `pnpm dev:mock` replays `fixtures/happy-path.jsonl` for the
  **Reverse-RPC** channel (`tools.invoke`) without spawning the Executa —
  handy for pure UI work. The HOST API channel still runs live.
- In **production** the first `anna.web.*` call returns `APP_NOT_GRANTED`
  until the user enables Web access in the app's permission card
  (Installed Apps → Permissions); the Executa's grant lives in its own
  Permissions modal.

## Layout

```
app.json                                   store listing + bundled_executas map
manifest.json                              schema 2 · ui.host_api.web + required_executas
bundle/                                    static-spa UI (index.html / app.js / style.css)
executas/web-search-via-executa-python/    the bundled Executa (stdio JSON-RPC)
fixtures/happy-path.jsonl                  tools.invoke mocks for `dev:mock`
```
