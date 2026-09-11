# anna-app-aps-files-demo

A minimal `schema: 2` Anna App that stores attachments in **Anna
Persistent Storage — APS Files** (object storage), demonstrating **both**
ways an app can reach object storage, switchable live via a radio toggle
in the UI:

```
Tool invoke (default, scope=user):
  iframe ── anna.tools.invoke ──▶ Executa ── files/* reverse-RPC ──▶ host ──▶ object store

HOST API (scope=app):
  iframe ── anna.files.* ─────────────────────────────────────────▶ host ──▶ object store
```

- **Tool invoke mode** (default, recommended): the UI calls
  `anna.tools.invoke` only. The bundled Executa
  (`executas/files-via-executa-python/`) owns the `aps.files` capability
  and performs the two-step presigned upload (`files/upload_begin` →
  HTTP `PUT` → `files/upload_complete`), plus `files/download_url` and
  `files/list`. Objects land under **`scope: "user"`**.
- **HOST API mode**: the UI calls `anna.files.upload_init` →
  browser-side `PUT` → `anna.files.upload_finalize`,
  `anna.files.download_url`, `anna.files.list` directly, using the app's
  own `ui.host_api.files` grant — plus **`anna.files.download`**, the
  host-mediated browser save (host ≥ 1.1.0-beta.97 / dispatcher ≥
  0.17.0): the host presigns with a forced
  `Content-Disposition: attachment`, opens the save dialog from the
  top-level page, and strips the URL before the iframe sees the result.
  Objects land in the app's own space (**`scope: "app"`**, host-forced).

Because the two modes write to different scopes (`user` vs `app`), notes
saved in one mode are **not** visible from the other — that is faithful
to production, where files are always self-owned by whoever the host
mints the `storage_token` for.

## Why this is the recommended pattern

An Anna App that wants to put bytes in object storage has two options:

1. **Ask for its own upload grant** — add `host.upload` / `host.files`
   to the app and request `ui.host_api.upload` / `ui.host_api.files`.
   Every install then prompts the user to trust the *app* with raw
   object-storage writes.
2. **Delegate to an Executa** (this demo) — the app requests **nothing**
   beyond `tools.invoke`. The Executa carries the `aps.files` capability,
   and the host mints a `storage_token` scoped to *the Executa*, routing
   `files/*` over the Executa's own reverse-RPC channel. The app iframe's
   ACL is never involved in the object-storage write.

Option 2 keeps the app's permission surface tiny and puts the storage
trust boundary on the Executa, where it belongs — that is why it is the
default mode of this demo. The **HOST API** radio flips to option 1 so
you can compare the two side by side. Supporting both is why the manifest
grants `ui.host_api.files` in addition to the executa-tools grant:

```json
"permissions": ["chat.write_message", "tools.invoke"],
"host_capabilities": ["aps.scope.user.read"],
"required_executas": [
  { "tool_id": "bundled:files-via-executa", "min_version": "0.1.0", "version": "latest" }
],
"ui": {
  "host_api": {
    "tools":  ["required:bundled:files-via-executa"],
    "files":  ["upload_init", "upload_finalize", "download_url", "download", "list", "delete"],
    "chat":   ["write_message"],
    "window": ["set_title"]
  }
}
```

> A real app that only needs the recommended pattern should drop the
> `files` line entirely and keep `tools` only — the `files` grant exists
> here purely to demonstrate the HOST API alternative. Exception: keep
> `files: ["download"]` + `host_capabilities: ["aps.scope.user.read"]`
> if you want the host-mediated browser save for Executa-generated
> (`scope=user`) artifacts — `files.download` is an app-iframe surface
> (the browser save dialog must be triggered by the host page), so it
> cannot be delegated to the Executa. `aps.scope.user.read` only ever
> exposes the current user's own rows (APS filters by `user_id`).

The bundled Executa declares `host_capabilities: ["aps.files"]` in its
`MANIFEST`. Without it the host refuses the `files/*` reverse-RPC with
`STORAGE_NOT_GRANTED`. The user must also have `storage_grant` enabled on
their `UserExecuta`.

---

## Run

```bash
pnpm install
```

Then:

```bash
# Real object storage. Requires developer credentials — the harness mints
# a real storage_token and round-trips files/* to /api/v1/storage/files/*.
anna-app login --host https://anna.partners   # one-time
pnpm dev:aps

# Mocked (offline, deterministic): the bridge intercepts tools.invoke and
# replays fixtures/happy-path.jsonl. The Executa subprocess does NOT issue
# real files/* calls in this mode.
pnpm dev:mock

# UI-only (LLM disabled):
pnpm dev:off
```

> **Without `--storage aps`** the in-memory dev harness has no
> object-storage backend, so a real `save_note` returns a clean
> `not_implemented` (JSON-RPC `-32004`) rather than failing obscurely.
> That is expected — switch to `pnpm dev:aps` to actually write bytes.

The harness opens the bundle in a Chromium iframe:

1. Pick a mode with the **Access mode** radios at the top — **Tool
   invoke** (default) or **HOST API** (`scope=app`). In Tool invoke mode,
   the **Executa scope** selector picks `user` (default) or `tool`.
2. Type an object **path** and **note text**, click **Save to APS
   Files**. The active path uploads the bytes and reports size + ETag.
3. Click **Get link** for a short-lived presigned `download_url`, or
   **List notes** to enumerate objects under the path's top-level prefix.
4. Click **Download** to trigger the host-mediated browser save
   (`anna.files.download`) — the save dialog is opened by the harness
   dashboard (top-level page), and the result shown in the iframe
   deliberately contains **no URL**. This answers “the Executa generated
   a file — how does the app download it?”: in **Tool invoke** mode with
   `scope=user` the app calls
   `anna.files.download({ path, scope: "user" })`, gated by the manifest
   `host_capabilities: ["aps.scope.user.read"]`; with `scope=tool` the
   object is plugin-private (app-side `files.download` is not exposed for
   tool scope), so the demo asks the **Executa** for a presigned link via
   `get_link` and opens that — the intended delegation path; in
   **HOST API** mode it reads the app's own `scope=app` object with no
   extra capability. Requires `@anna-ai/cli` ≥ 0.1.39 (harness) or Anna
   host ≥ 1.1.0-beta.97 (production).

> **HOST API uploads `PUT` straight from the browser** to the
> host-issued presigned R2 URL, so the R2 bucket needs CORS allowing the
> dev origin (configured in production). `download_url` and `list` route
> through the host REST API and work regardless. In **Tool invoke** mode
> the `PUT` happens inside the Executa subprocess, so no browser CORS is
> involved.

---

## Tools exposed by the Executa

| Tool         | Maps to                                   | Returns                              |
| ------------ | ----------------------------------------- | ------------------------------------ |
| `save_note`  | `upload_begin` → PUT → `upload_complete`  | `{ path, scope, size_bytes, etag }`  |
| `get_link`   | `download_url`                            | `{ path, scope, url, expires_at }`   |
| `list_notes` | `list`                                    | `{ scope, items[], next_cursor }`    |

Every tool accepts an optional `scope` argument — `"user"` (default,
user-wide so the user can find the notes again from the Anna chat UI) or
`"tool"` (plugin-private, an isolated per-executa bucket for caches and
internal state). Passing `"app"` is rejected with a clear error: plugin
`storage_token`s never cover `app` scope — that scope belongs to the
App-side Host API, which is exactly what **HOST API** mode demonstrates
(host-forced `scope: "app"`, separate from both Executa scopes).

---

## Files

```
anna-app-aps-files-demo/
  app.json                 # publish metadata + bundled_executas map
  manifest.json            # schema:2 app manifest (tools-only host_api)
  package.json             # dev scripts
  bundle/                  # static-spa UI (index.html, app.js, style.css)
  executas/
    files-via-executa-python/
      executa.json         # publish + dev metadata + distribution (install) block
      pyproject.toml       # uv/hatchling build; script name == tool_id
      files_via_executa_plugin.py
  fixtures/
    happy-path.jsonl       # offline mock responses for tools.invoke
```

---

## Publish & Install (production distribution)

`anna-app dev` runs the Executa straight from this project dir, so it works
with **no** distribution config. To make the app installable for real users,
the bundled Executa must declare **how the Agent fetches it** — otherwise a
published Executa has no distribution locator and stays dev-only.

That locator lives in `executas/files-via-executa-python/executa.json` under
the `distribution` block:

```json
"distribution": {
  "type": "uv",
  "package_name": "tool-test-files-via-executa-12345678",
  "executable_name": "tool-test-files-via-executa-12345678",
  "supports_protocol": true,
  "capabilities": ["save_note", "get_link", "list_notes"]
}
```

`anna-app apps push` (and `apps publish`) registers each bundled Executa
first via `executa publish`, which now **forwards this block** onto the
server `Executa` row (`distribution_type` / `package_name` /
`executable_name` / `supports_protocol` / `binary_urls` / `capabilities`).
The minted `tool_id` is then substituted into every `bundled:files-via-executa`
reference in the manifest. End-to-end:

```bash
anna-app login --host https://anna.partners      # one-time
anna-app apps push                               # upsert working draft
#   ├─ registers executas/files-via-executa-python (distribution → server row)
#   ├─ substitutes bundled:files-via-executa → minted tool_id
#   └─ PUT /developer/apps/{id}/working + stages the UI bundle
anna-app apps cut 0.1.0                           # freeze an immutable version
anna-app apps release 0.1.0                       # promote to the store
```

At install time the Agent reads `distribution_type` + `package_name` and
runs the full v2 install pipeline (here: `uv` installs the published package
from a Python index into `~/.anna/executa/tools/`). The package name **must**
match `[project.name]` / `[project.scripts]` in
[`pyproject.toml`](executas/files-via-executa-python/pyproject.toml) and the
minted `tool_id`.

### Distribution `type` cheat-sheet

| `type`     | Agent installs via             | Requires                                 |
| ---------- | ------------------------------ | ---------------------------------------- |
| `uv`       | `uv` from a Python index       | `package_name`                           |
| `pipx`     | `pipx` from a Python index     | `package_name`                           |
| `npm`      | `npm` from the npm registry    | `package_name`                           |
| `homebrew` | Homebrew formula               | `package_name`                           |
| `binary`   | per-platform download + verify | `binary_urls` (URL or `{url,sha256,…}`)  |
| `local`    | a local `.tar.gz` archive      | (dev iteration only)                     |

To ship a self-contained binary instead of a `uv` package — no "install
Python first" — swap the block to:

```json
"distribution": {
  "type": "binary",
  "supports_protocol": true,
  "binary_urls": {
    "darwin-arm64": { "url": "https://…/tool-darwin-arm64.tar.gz", "sha256": "…" },
    "linux-x86_64": "https://…/tool-linux-x86_64.tar.gz"
  }
}
```

> Building + uploading the binary assets to a host the Agent can reach is a
> **separate** pipeline from the app bundle — see the matrix-nexus
> `executa-binary` docs. `executa publish` only records the locator; it does
> not upload bytes.

---

## Notes for plugin authors

- **`describe` returns the bare manifest** — matrix's
  `ToolManifest.from_dict` reads `result["name"]` directly. Wrapping it
  in `{"manifest": ...}` drops the plugin at load time.
- **`invoke` results are wrapped** as `{"success": true, "tool", "data"}` —
  returning the bare tool dict makes the host read `success=False`.
- **Tool params use `parameters: [...]`**, not MCP-style `input_schema`.
  The host only reads `parameters`; using `input_schema` makes the LLM
  see an argument-less tool and hallucinate keys.
- The Executa PUTs bytes **directly** to the host-issued presigned URL —
  the payload never proxies through the host process.
