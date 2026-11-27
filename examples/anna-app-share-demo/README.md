# anna-app-share-demo

Demonstrates **public share links** — the `share.*` Host API
(design: matrix-nexus `docs/design/app-public-share-static-hosting.md`;
reference: [Public Share Links](https://anna.partners/developers/apps/app-public-share)).

An App that renders static content (slides, reports, documents) can publish
the finished files to a **platform-hosted, long-lived, revocable public
URL**. Anyone with the link opens it in a plain browser — no Anna account,
no App install, no model calls. The platform is a dumb static host: the
snapshot must be self-contained (viewer JS/CSS baked in).

## What's inside

- `bundle/` — static single-window UI covering the full lifecycle:
  1. **Generate & upload** — renders a tiny self-contained HTML document +
     stylesheet and uploads both to the app's own APS object space
     (`anna.files.upload_init` → presigned PUT → `upload_finalize`).
  2. **Publish** — `anna.share.publish({ paths, entry, title, … })`
     snapshots the finalized objects into a public URL
     (`https://shares.anna.page/s/<pub_id>/` in production).
  3. **Publications list** — `anna.share.list` with per-row actions:
     - **Republish** — `publish` with `pub_id`: same URL, `version += 1`;
     - **Rename** — `anna.share.update` (metadata only, snapshot untouched);
     - **Revoke** — `anna.share.revoke`: the URL becomes a tombstone
       immediately (CDN residue ≤ 5 minutes), quota bytes are released.
- `manifest.json` — the capability declaration that gates all of this:

  ```jsonc
  "ui": { "host_api": { "share": true } }
  ```

  `share.*` is one of the four second-principal declarations (it publishes
  user content to the open internet). Calls without it fail with
  `share_not_declared`.

## Run it locally

```bash
cd examples/anna-app-share-demo
npm install
npm run dev:aps        # anna-app dev --storage aps  (real APS object storage)
```

The local harness ships an in-memory `share.*` stub: `publish` fetches your
uploaded objects through the same APS files path and serves the snapshot on
the harness origin at `/s/<pub_id>/…`, so the returned URL opens in your
browser exactly like production. Publications do not survive a harness
restart.

Without `--storage aps` the stub cannot read any objects and `publish`
fails with a clear `share_not_found` error — that is expected.

## Limits worth knowing (production)

≤ 100 MB per snapshot · ≤ 200 files · ≤ 50 MB per file · ≤ 20 active
publications per (user, app) · ≤ 10 publishes per hour · snapshot bytes
count against the user's APS quota (released on revoke). Entry must be
`text/html`; content types outside the whitelist are rejected.
