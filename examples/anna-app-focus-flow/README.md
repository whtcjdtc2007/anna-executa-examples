# Focus Flow — Anna App example

> Pomodoro / deep-work timer packaged as an installable **Anna App**. Bundles a
> stdio Tool plugin (`focus-session`), a coaching Skill (`focus-coach`), and a
> premium UI bundle that renders inside Anna's UI Runtime sandbox.
>
> The plugin ships in **three flavours** — Python, Node.js and Go — all
> conforming to the same JSON-RPC contract. Pick the one that matches your
> stack, and use the same example to verify the harness works against all
> three runtimes.

[简体中文](./README.zh-CN.md)

---

## Choose your language

The App's UI bundle, manifest and Skill are language-agnostic. The
`focus-session` Tool plugin ships in three flavours; the `enabled` field
in each `executa.json` decides which one `anna-app dev` launches:

| Flavour | Directory                                                              | Default `enabled` | Runtime requirement |
| ------- | ---------------------------------------------------------------------- | ----------------- | ------------------- |
| Python  | [`executas/focus-session-python/`](./executas/focus-session-python/)   | `true`  | `uv` 0.1+ on PATH |
| Node.js | [`executas/focus-session-node/`](./executas/focus-session-node/)       | `false` | `node` 18+ on PATH (no `npm install` needed) |
| Go      | [`executas/focus-session-go/`](./executas/focus-session-go/)           | `false` | `go` 1.21+ (or a pre-built binary) |

All three share the same `~/.anna/focus-flow/state.json`, the same
`session` tool surface and the same InvokeResult envelope — the bundle
never knows which backend is running. To switch flavours declaratively,
flip `enabled` to `true` for the one you want and `false` for the other
two (only one can be active at a time; `anna-app dev` warns on duplicate
`tool_id`s).

You can also leave `enabled` alone and pick the flavour for a single
run via the CLI flag — `--executa` overrides `enabled: false`:

```bash
anna-app dev --executa dir=./executas/focus-session-node
anna-app dev --executa dir=./executas/focus-session-go,type=go
```

For the full discovery rules and `executa.json` schema see
[`docs/multi-language-anna-apps.md`](../../docs/multi-language-anna-apps.md).

---

## What's inside

```
anna-app-focus-flow/
├── app.json                          # App metadata (slug, name, category…)
├── manifest.json                     # AppManifest (schema:2)
├── scripts/
│   └── set-tool-id.py                # apply / reset minted IDs across the Python flavour files
├── bundle/                           # static-spa UI loaded by UI Runtime
│   ├── index.html
│   ├── style.css
│   ├── app.js                        # talks to anna.* RPC SDK
│   └── icon.svg
└── executas/
    ├── focus-session-python/         # stdio Tool plugin — Python / uv (default)
    │   ├── executa.json              #   {tool_id, type:"python", enabled:true}
    │   ├── pyproject.toml
    │   ├── focus_session_plugin.py
    │   └── README.md
    ├── focus-session-node/           # stdio Tool plugin — Node.js 18+
    │   ├── executa.json              #   {tool_id, type:"node", enabled:false}
    │   ├── package.json
    │   ├── focus_session_plugin.js
    │   └── README.md
    ├── focus-session-go/             # stdio Tool plugin — Go 1.21+
    │   ├── executa.json              #   {tool_id, type:"go", enabled:false}
    │   ├── go.mod
    │   ├── main.go
    │   └── README.md
    └── focus-coach/
        └── SKILL.md                  # declarative Skill (YAML frontmatter)
```

## How the pieces connect

```
┌──────────────┐    anna.tools.invoke    ┌──────────────────────┐
│ bundle/app.js│ ──────────────────────▶ │ Anna UI Runtime      │
│  (sandbox)   │ ◀─────────────────────  │   ↳ host dispatcher  │
└──────────────┘    JSON-RPC result      └──────────┬───────────┘
                                                    │ NATS
                                                    ▼
                                       ┌─────────────────────────────────┐
                                       │ executas/focus-session-{python, │
                                       │   node, go}  — pick one via     │
                                       │   executa.json (stdio plugin)   │
                                       └─────────────────────────────────┘
```

The Skill (`focus-coach`) is loaded into the LLM's system prompt by Anna
whenever the app's window is focused, and it instructs the assistant on when /
how to call the tool.

---

## Tool surface — single dispatcher method

The `focus-session` plugin exposes **one** tool method (`session`) with an
`action` discriminator. Each Executa registered on Anna corresponds to a
running plugin (matched by its server-minted `tool_id`); the bundle then
picks which plugin method to call via the `method` arg on `tools.invoke`.
Keeping the plugin to a single dispatcher method means there's only ever
**one** Executa row per app, and the bundle just toggles `action`.

| `action`     | Args                          | Returns                                       |
| ------------ | ----------------------------- | --------------------------------------------- |
| `start`      | `duration_minutes`, `topic?`  | `{ active }`                                  |
| `pause`      | —                             | `{ active }`                                  |
| `resume`     | —                             | `{ active }`                                  |
| `complete`   | `notes?`                      | `{ completed, today }`                        |
| `get_state`  | —                             | `{ active, today, recent }`                   |

State persists to `~/.anna/focus-flow/state.json`. See
[executas/focus-session-python/README.md](./executas/focus-session-python/README.md) for the
stdio JSON-RPC contract.

---

## AppManifest essentials

`manifest.json` is validated by Anna's [`AppManifest`][schema] Pydantic model
(`extra="forbid"`) plus a static UI validator. The fields used here:

```json
{
  "schema": 1,
  "permissions": ["tools.invoke", "chat.write_message", "storage.read",
                  "storage.write", "ui.svg"],
  "required_executas": [
    { "tool_id": "tool-CHANGEME-focus-session-CHANGEME", "min_version": "1.0.0" },
    { "tool_id": "skill-CHANGEME-focus-coach-CHANGEME" }
  ],
  "ui": {
    "bundle": { "format": "static-spa", "entry": "index.html" },
    "views": [{ "name": "main", "title": "Focus Flow", "default": true,
                "min_size": {"w":360,"h":520},
                "default_size": {"w":480,"h":640},
                "max_size": {"w":720,"h":960},
                "icon": "icon.svg" }],
    "host_api": {
      "tools":   ["required:tool-CHANGEME-focus-session-CHANGEME"],
      "chat":    ["write_message"],
      "storage": ["get", "set"],
      "window":  ["set_title"]
    }
  }
}
```

> **Mint your own IDs first.** Both `tool_id`s above are placeholders.
> Mint each Executa at <https://anna.partners/executa> (→ *My Tools* /
> *My Skills* → **Create** → **🪪 Mint**), then paste the minted strings
> into `manifest.json` (`required_executas` + `host_api.tools`) and into
> `bundle/app.js` (`TOOL_ID`).

### Tool ID & ACL invariants

Verified against
`matrix-nexus/src/services/anna_app_rpc_dispatcher.py`:

- **Mint-only `tool_id`s.** Anna mints `tool_id` server-side as
  `tool-{handle}-{slug}-{uniq}` / `skill-{handle}-{slug}-{uniq}`; clients
  cannot pick or override it. The minted string is the only valid
  identifier in `required_executas[].tool_id`.
- The bundle calls
  `anna.tools.invoke({ tool_id, method, args })`. The dispatcher uses
  the entire `tool_id` as the NATS plugin name and `method` as the
  target tool inside that plugin. The legacy dotted form
  `tool_id="plugin.method"` still works as a back-compat fallback when
  no `method` arg is supplied.
- `_is_tool_allowed` does **literal equality** between `tool_id` and the
  strings in `required_executas[].tool_id` / `optional_executas[].tool_id`
  (after honoring `required:*` / `optional:*` wildcards). There is no
  prefix-matching of plugin names.

`host_api.tools` accepts `["required:*"]`, `["optional:*"]`, `"required:<id>"`,
`"optional:<id>"`, or a bare `"<id>"` — the bare and prefixed forms must
appear in `required_executas` / `optional_executas`.

### `host_api` reality check

The host dispatcher's real surface (verified against the dispatch table):

| ns         | methods                                                                |
| ---------- | ---------------------------------------------------------------------- |
| `tools`    | `list`, `invoke({ tool_id, method, args })`                            |
| `chat`     | `write_message({ role, content })`, `append_artifact`, `read_history`  |
| `storage`  | `get({ key })`, `set({ key, value })`, `delete({ key })`               |
| `window`   | `hello`, `ready`, `set_title({title})`, `resize({w,h})`, `focus`, `close({reason})`, `open_view({view, payload})`, `report_error` |
| `artifact`, `llm`, `fs`, `prefs` | currently stubbed (`not_implemented`)            |

Notes:

- `window.hello`, `window.ready`, and `window.report_error` are auto-allowed
  even if not listed in `host_api.window` (they're in `_NO_AUTH_NEEDED`).
- The whole `window` namespace is special-cased to be allowed regardless of
  the `host_api.window` allow-list — listing methods there is informational.
- `permissions[]` at the manifest root is a list of free-form strings used
  for display / audit; **runtime ACL is enforced by `host_api.*`**. The
  validator does, however, restrict `permissions[]` to a known vocabulary
  — use `storage.read` / `storage.write` (not `storage.get` / `storage.set`)
  here, even though the runtime methods are `storage.get` / `storage.set`
  / `storage.delete`.

---

## SDK call shapes used by `bundle/app.js`

The bundle loads the runtime SDK from
`/static/anna-apps/_sdk/0.1.0/index.js` (global: `AnnaAppRuntime`) and
connects with:

```js
const anna = await AnnaAppRuntime.connect();
// ↑ requires `wid` and `t` URL params, which Anna injects when it opens
//   the iframe. Standalone preview throws — bundle falls back to a stub.
```

| What we want         | Real SDK call                                                                                  |
| -------------------- | ---------------------------------------------------------------------------------------------- |
| Invoke the tool      | `anna.tools.invoke({ tool_id: "<minted>", method: "session", args: {...} })`                   |
| Read storage         | `const { value } = await anna.storage.get({ key })`                                            |
| Write storage        | `await anna.storage.set({ key, value })`                                                       |
| Append a chat line   | `await anna.chat.write_message({ role: "user", content: "..." })`                              |
| Update window title  | `await anna.window.set_title({ title })`                                                       |
| Signal ready         | auto-emitted by `AnnaAppRuntime.connect()` — no manual call needed                             |

There is **no** `window.set_summary`, **no** `storage.read/write`, and the
`tools.invoke` envelope is not `{tool, method: ..., arguments}` — those would
all be rejected by the dispatcher. (The `method` field above is a
**plugin** method, not a JSON-RPC envelope key.)

---

## Install — Tool plugin (`focus-session`)

The plugin is a stdio Executa using JSON-RPC over `stdin`/`stdout`. The
recommended distribution is `uv tool install` (also supported by Anna:
`pipx`, `binary`, `local`). The minted Tool ID has to appear **identically**
in four places — `pyproject.toml`'s `[project].name` and
`[project.scripts]` key, the plugin's `MANIFEST["name"]`,
`manifest.json` (`required_executas[].tool_id` + `ui.host_api.tools`), and
`bundle/app.js`'s `TOOL_ID`. The repo ships with `*-CHANGEME-*`
placeholders and a helper that flips all four atomically.

```bash
cd executas/focus-session-python
# 1) Smoke-test the placeholder build (does not need the minted ID yet):
uv tool install . --reinstall
echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' \
    | tool-CHANGEME-focus-session-CHANGEME
uv tool uninstall tool-CHANGEME-focus-session-CHANGEME    # clean up
```

Then register it as an Executa at <https://anna.partners/executa>:

1. Open **My Tools** → click **Create Tool**, pick type `tool`, fill in a
   *display name* (e.g. `Focus Session`). The form's *Name* field is
   cosmetic — the routing identity comes from the minted Tool ID, not
   from this label.
2. Click the **🪪 Mint** button next to the Tool ID field.
   - Anna reserves a server-controlled ID like
     `tool-{your-handle}-focus-session-{uniq}` and locks it to your account.
   - **You cannot type a custom ID.** The input is read-only and the
     **Create** button stays disabled until Mint succeeds. Any `tool_id`
     a client tries to send is silently dropped server-side.
3. Wire the minted ID into all four files at once:

   ```bash
   # From the example root:
   scripts/set-tool-id.py apply --tool tool-yourhandle-focus-session-abcd1234
   scripts/set-tool-id.py status   # sanity check
   ```

4. Re-install the plugin under its new name and confirm the shim resolves:

   ```bash
   cd executas/focus-session-python
   uv tool install . --reinstall --no-cache
   which tool-yourhandle-focus-session-abcd1234   # → ~/.local/bin/<minted>
   echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' \
       | tool-yourhandle-focus-session-abcd1234
   ```

   `describe` must return `manifest.name == <minted tool_id>`; otherwise
   the Agent shows a Stopped card and the bundle's `tools.invoke` cannot
   route. (`set-tool-id.py` already updates `MANIFEST["name"]`.)

5. Back on the form, fill in the *Distribution* section to match how you
   actually installed the plugin:

   | Distribution | What `package_name` means                | When to use it                                           |
   | ------------ | ---------------------------------------- | -------------------------------------------------------- |
   | `uv`         | uv install spec (here: the minted ID)    | You ran `uv tool install .` above. **Recommended.**      |
   | `pipx`       | pipx install spec (the minted ID)        | You ran `pipx install .` instead.                        |
   | `binary`     | unused — see `binary_urls` per-OS map    | You shipped a downloadable archive for each OS.          |
   | `local`      | absolute path to a local archive on disk | Air-gapped / dev-only; archive already on the Agent box. |

   For the `uv` flow, set both `package_name` **and** `executable_name`
   to the minted ID. matrix-nexus uses `executable_name` as the argument
   to `shutil.which(...)` on the Agent host, so it must match the
   `[project.scripts]` entry point name.

6. Paste the manifest returned by `describe` (matrix-nexus also re-fetches
   it after save), then click **Create** to commit the draft.

> **Re-installing after editing the plugin:**
> `uv tool install --reinstall .` (plain `uv tool install .` is a no-op
> when the version hasn't bumped, so old bytecode would keep running).

> **Resetting the repo before committing:** run
> `scripts/set-tool-id.py reset` to revert all four files back to the
> `*-CHANGEME-*` placeholders so the example stays publishable.

## Install — Skill (`focus-coach`)

1. At <https://anna.partners/executa>, open **My Skills** → **Create Skill**.
2. Name it (e.g. `focus-coach`), pick type `skill`, click **🪪 Mint** to
   reserve a server-minted ID like `skill-{your-handle}-focus-coach-{uniq}`.
3. Paste / upload `executas/focus-coach/SKILL.md` as the skill content,
   then **Create**.
4. Copy the minted skill ID into `manifest.json` (`required_executas`).

## Install — Anna App

1. <https://anna.partners/executa> → **My Apps** → **Create App**.
2. On the **Listing** tab, fill in `app.json` fields (slug = `focus-flow`,
   category = `productivity`) and save.
3. Create a version on the **Versions** tab:
   - Click *Create*, paste the contents of `manifest.json` into the
     manifest text box, set the version string, and submit. The manifest
     is sent as a JSON object — there is no separate file upload for it.
   - On the new version row, click **Bundle** and upload every file under
     `bundle/` (`index.html`, `app.js`, `style.css`, `icon.svg`, …) through
     the bundle uploader. Files are streamed directly to object storage
     and finalized server-side.
   - Make sure each `required_executas[].tool_id` in the manifest is
     **literally identical** to the Tool / Skill IDs you minted above
     (the runtime dispatcher does strict string equality).
4. matrix-nexus runs three validation layers on the manifest:
   - `AppManifest` (Pydantic v2, `extra="forbid"`) for shape & types.
   - `validate_ui_section_static` for CSP, view geometry, and the rule
     that every `host_api.tools` entry must resolve to a declared
     `required_executas` / `optional_executas` ID.
   - A DB check that every referenced Executa exists and its visibility
     allows app bundling (`app_bundled` or `public`).
5. **Submit for review** → wait for an admin to *Approve* the app →
   **Publish** the version → **Install** from the app's detail page →
   open it from your sidebar. Publish is rejected until the app reaches
   the `APPROVED` (or already `PUBLISHED`) state.

Both Executas (the minted Tool and Skill IDs) must be installed in the
user's account before app install succeeds — Anna refuses installs whose
`required_executas` aren't all present.

---

## Local development

The recommended loop uses [`@anna-ai/cli`](https://www.npmjs.com/package/@anna-ai/cli)
(installed as a devDependency in this example's `package.json`). It spawns the
harness on `http://localhost:5180`, auto-discovers the executa under
`executas/focus-session-python/`, and proxies `anna.*` RPCs to a real Python bridge
(`anna-app-runtime-local`) — exactly the same surface Anna uses in production.

### 1. One-time setup

```bash
# from the repo root
pnpm install                # installs @anna-ai/cli for every example
uv --version                # uv is required to spawn the bridge / executas
anna-app doctor             # checks uv, runtime pin, etc.
```

> If `which anna-app` finds nothing, run scripts via `pnpm` (e.g.
> `pnpm --filter anna-app-focus-flow dev`) so the local CLI binary is on PATH.

### 2. Run the dev harness

```bash
cd examples/anna-app-focus-flow
pnpm dev                    # → anna-app dev
# Harness UI:        http://localhost:5180
# RPC log panel:     right side of the harness window
# Bundle hot-reload: edits under bundle/ trigger reload
```

On first `tools.invoke`, the bridge lazy-spawns the executa with
`uv run --project executas/focus-session-python <minted-tool-id>`. If the executa
process exits immediately, the harness surfaces `tool_failed: executa
process exited` in the RPC log — `cd executas/focus-session-python && uv sync`
will print the real dependency-resolution error.

### 3. Validate the manifest

```bash
pnpm validate               # → anna-app validate --strict
```

This runs the same three layers the server applies on submission
(`AppManifest` Pydantic model + `validate_ui_section_static` + bundle
file checks).

### 4. Run the contract tests

The pytest suite in `tests/plugin/` uses the published
[`anna-executa-test`](https://pypi.org/project/anna-executa-test/) helper to
spawn the plugin and assert the JSON-RPC contract.

```bash
cd executas/focus-session-python
uv sync --extra dev         # installs pytest + anna-executa-test
uv run pytest ../../tests/plugin -q
```

The vitest suite (`pnpm test`) covers the bundle / fixture parsing.

### 5. Replay recorded fixtures

```bash
pnpm fixture:verify         # replay fixtures/*.jsonl through the harness
pnpm fixture:summarize      # human-readable transcript of the happy path
```

### Lower-level checks (debug only)

Drive the plugin's stdio JSON-RPC directly, bypassing the harness:

```bash
cd executas/focus-session-python
uv run python focus_session_plugin.py <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"describe"}
{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"tool":"session","arguments":{"action":"start","duration_minutes":1,"topic":"smoke test"}}}
{"jsonrpc":"2.0","id":3,"method":"invoke","params":{"tool":"session","arguments":{"action":"get_state"}}}
EOF
```

Preview the bundle without host RPCs (a toast warns the SDK is unavailable;
the layout still renders):

```bash
cd bundle && python -m http.server 8080   # http://localhost:8080
```

---

## License

MIT — see [`LICENSE`](../../LICENSE).

[schema]: https://github.com/openclaw/matrix-nexus/blob/main/src/schemas/anna_app.py
