# anna-app-frame-embed-demo

The **smallest possible UI-only Anna App** — and the reference for embedding
third-party iframes via the scoped `frame-src` CSP override (forum #343).

- No executas. No Host API calls. No grants. No fixtures.
- One manifest declaration + one `<iframe allow=...>` attribute.

## What it demonstrates

### 1. `csp_overrides["frame-src"]` — the declaration

By default an App bundle is served with `default-src 'none'` and **no**
`frame-src`, so every nested iframe is blocked. Declaring explicit origins
lifts that for those origins only:

```jsonc
"ui": {
  "csp_overrides": {
    "frame-src": ["https://www.youtube-nocookie.com"]
  }
}
```

Rules (enforced by `anna-app validate` and the server): explicit
`https://host[:port]` origins only — no `*` / wildcard subdomains, no paths,
no CSP keywords (`'self'`, …), max 8 origins. Declared origins are disclosed
on the App's install/review surface, like `external_origins`.

### 2. The playback permission delegation chain

Declaring `frame-src` also makes the platform scope four playback features —
`autoplay`, `encrypted-media`, `fullscreen`, `picture-in-picture` — to your
declared origins:

```
Permissions-Policy response header        (platform, automatic)
  → host window iframe allow attribute    (platform, automatic)
    → YOUR bundle's <iframe allow="...">  (you — see bundle/index.html)
      → the embedded player
```

The last hop is yours. Without the `allow` attribute the player renders but
the fullscreen button and autoplay-on-seek **silently fail**:

```html
<iframe
  src="https://www.youtube-nocookie.com/embed/VIDEO_ID"
  allow="autoplay; encrypted-media; fullscreen; picture-in-picture"
></iframe>
```

### 3. Jump to cited timestamps

The chapter buttons rebuild the embed URL with `?start=N&autoplay=1` — the
exact "play the cited video moment" pattern from forum #343. Playback resumes
across jumps only because `autoplay` is delegated end-to-end.

The demo video is [Big Buck Bunny](https://peach.blender.org/) (Blender
Foundation, CC-BY) — a stable, never-taken-down upload.

## Run it

```bash
pnpm install          # or npm install
pnpm validate         # anna-app validate — checks the frame-src declaration
pnpm dev              # anna-app dev — local harness
```

## ⚠️ Local harness parity trap

`anna-app dev` serves your bundle **without** CSP or Permissions-Policy
headers. Embeds therefore work locally **even if you delete the `frame-src`
declaration** — the block only appears in production. Always:

1. run `anna-app validate` (catches a missing/invalid declaration statically);
2. verify against an **online working draft** before shipping.

To see the production failure mode: publish a working draft *without* the
declaration and watch DevTools log
`Refused to frame 'https://www.youtube-nocookie.com/' because it violates the
following Content Security Policy directive: "default-src 'none'"`.

## Tips

- Prefer `https://www.youtube-nocookie.com` over `https://www.youtube.com` —
  same player, fewer ambient cookies sent to Google.
- The embedded page is a normal cross-origin iframe: it inherits the app
  sandbox, cannot reach the Anna host bridge, and cannot call Host APIs.
- Docs: [App UI Manifest — Embedding third-party content](https://anna.partners/developers/apps/app-ui-manifest#embedding-third-party-content-frame-src).
