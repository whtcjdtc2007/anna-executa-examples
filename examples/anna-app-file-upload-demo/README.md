# anna-app-file-upload-demo

A `schema: 2` Anna App (display name **Host Upload Demo**) that demonstrates
the **`host/uploadFile`** reverse-RPC — **`inline`**, **`negotiate`** and
**`confirm`** — persisting a binary blob to **short-lived** host storage
(~30 min TTL, not enumerable).

> Reference:
> [Executa · Host Upload](https://anna.partners/developers/reference/executa-host-upload).

```
iframe ── tools.invoke(make_sample) ─────▶ Executa ── writes scratch file ─▶ local disk
iframe ── tools.invoke(host_upload_path) ▶ Executa ── host/uploadFile ──────▶ host ──▶ R2
                                              │ inline (≤8 MiB, base64 round-trip)
                                              │ negotiate → PUT (plugin→R2) → confirm
                                              ▼
                                         short-lived download URL (~30 min TTL)
```

## Why bytes are sourced locally, not from the browser

`host/uploadFile` persists a blob the **Executa already holds** — an image it
generated, a screenshot, an intermediate PDF — and in `negotiate` mode the
bytes stream **plugin → R2 directly**, never touching the JSON-RPC stdio
channel.

A browser-picked `File` can only reach a subprocess **over that stdio
channel**. Shipping it as base64 through `anna.tools.invoke` makes the bytes
cross stdio twice (in via the invoke request; back out via the reverse-RPC
request for `inline`), which caps the practical file size at the stdio line
limit and defeats the whole point of `negotiate`. So this demo flips the
model: the iframe sends only **small control messages** (a size, or a local
path) and the **Executa supplies the bytes**. That is what lets
`host/uploadFile` scale past the stdio limit.

## The three modes

| Mode                | When                     | Path                                                                 |
| ------------------- | ------------------------ | -------------------------------------------------------------------- |
| `inline`            | payload ≤ 8 MiB          | one reverse-RPC round-trip, bytes ride as base64                     |
| `negotiate`         | payload > 8 MiB          | host signs an R2 `PUT` URL; the Executa streams the file to R2       |
| `confirm`           | after a `negotiate` PUT  | host HEADs R2, registers the object, returns the short-lived link    |

All three return a short-lived HTTPS GET URL. Store the `r2_key`, **not** the
`download_url` — URLs TTL out (~30 min), keys are stable for the object's
lifetime.

## Tools exposed by the Executa

| Tool               | Does                                                                                     | Returns                                                       |
| ------------------ | --------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `make_sample`      | Write a scratch file of `size_bytes` bytes to the local filesystem.                     | `{ ok, path, filename, size_bytes }`                         |
| `host_upload_path` | Read a **local file path** and persist it via `host/uploadFile` (auto inline/negotiate). | `{ ok, mode, filename, size_bytes, mime_type, download_url, r2_key, expires_at }` |

`make_sample` exists only so the UI can conjure preset-size payloads that
straddle the thresholds without committing large binaries to the repo. A real
Executa would already have the bytes (a rendered image, a downloaded asset)
and call `host_upload_path` directly.

The Executa declares `host_capabilities: ["host.upload"]`. Without it the host
refuses `host/uploadFile` with `UPLOAD_NOT_GRANTED` (-32201). The user must
also have `upload_grant` enabled on their `UserExecuta`.

## Quotas & MIME policy (`upload_grant`)

The host enforces the user's grant before any R2 round-trip:

- **Per-file size cap** — `25 MiB` under `anna-app dev` (the doc-default is
  `20 MiB`). Over the cap &rarr; `UPLOAD_TOO_LARGE` (-32204). The **30 MiB**
  preset deliberately trips it.
- **MIME hard denylist only** — there is **no whitelist**: any type is
  accepted unless it is on the host's hard denylist (executables,
  `image/svg+xml`) &rarr; `UPLOAD_MIME_REJECTED` (-32210). Office documents,
  custom binary formats etc. all upload fine. Declare the file's real MIME:
  non-media types are served with `Content-Disposition: attachment` (signed
  into the download URL), while `image/*` / `audio/*` / `video/*` /
  `application/pdf` render inline. The synthetic samples upload as
  `application/pdf`; the upload-by-path field infers the MIME from the file
  extension.

This demo does not change the quota.

---

## Run

```bash
pnpm install
```

Then:

```bash
# Real Host Upload. Needs a signed-in account so the host can mint an
# upload_token — but, unlike the APS files/* flow, does NOT need --storage aps.
anna-app login --host https://anna.partners   # one-time
pnpm dev

# Mocked (offline, deterministic): the bridge intercepts tools.invoke and
# replays fixtures/happy-path.jsonl. No real host/uploadFile is issued.
pnpm dev:mock

# UI-only (LLM disabled):
pnpm dev:off
```

The harness opens the bundle in a Chromium iframe:

1. **Preset uploads** — click a size button. The Executa `make_sample`s a
   payload of that size and `host_upload_path`s it. Watch the **mode** flip
   from `inline` (1/6 MiB) to `negotiate+confirm` (12/18 MiB) across the 8 MiB
   cap, and the **30 MiB** button trip `UPLOAD_TOO_LARGE` against the per-file
   quota.
2. **Upload a local file by path** — type an absolute path the Executa can
   read on this machine, pick a `purpose`, and click **Upload path**.

Each result shows the size + chosen mode and, on success, the short-lived
`open ↗` link. Host Upload objects are transient and **never** listed — the
returned link is the only deliverable.

> **No browser CORS is involved.** Unlike a browser-side `PUT`, the `negotiate`
> `PUT` happens inside the Executa subprocess, so the R2 bucket does not need
> to allow the dev origin.

## Permission surface

```json
"permissions": ["chat.write_message", "tools.invoke"],
"required_executas": [
  { "tool_id": "bundled:file-upload-via-executa", "min_version": "0.1.0", "version": "latest" }
],
"ui": {
  "host_api": {
    "tools":  ["required:bundled:file-upload-via-executa"],
    "chat":   ["write_message"],
    "window": ["set_title"]
  }
}
```

The app itself requests **nothing** beyond `tools.invoke` — no upload grant of
its own. All host storage is reached through *the Executa's* `host.upload`
capability, keeping the app's permission surface tiny. That is the recommended
pattern: put the storage trust boundary on the Executa, where it belongs.

## See also

- [`anna-app-aps-files-demo`](../anna-app-aps-files-demo) — **durable**,
  enumerable per-user object storage via the `files/*` reverse-RPC
  (`upload_begin` → PUT → `upload_complete`). Use that for state the user
  should find again; use **this** demo's `host/uploadFile` for throwaway
  invoke-scoped artefacts.
