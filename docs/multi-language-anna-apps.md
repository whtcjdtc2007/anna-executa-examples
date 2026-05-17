# Multi-language Anna Apps — `executa.json` & discovery

`anna-app dev` (the local-harness CLI shipped in [`@anna-ai/cli`][cli]) runs your
Anna App with a real `anna-app-runtime-local` Python bridge that lazy-spawns
each Tool plugin (Executa) as a subprocess on first `tools.invoke`. From v0.1.10
onward the discovery layer is **language-agnostic**: the same App can ship
Python, Node.js, Go, or pre-built binary executas under `executas/<name>/`,
and each one is launched the way it asks to be launched.

This document describes:

1. The discovery rules `anna-app dev` applies to subdirectories of
   `<manifest-dir>/executas/`.
2. The `executa.json` schema (the explicit, language-agnostic config file).
3. The `--executa <spec>` CLI flag for one-off / out-of-tree registrations.
4. How the `command` you declare reaches the bridge (and what defaults the
   bridge picks if you omit it).

[cli]: https://www.npmjs.com/package/@anna-ai/cli

---

## 1 · Discovery order

For each subdirectory under `<manifest-dir>/executas/`, the CLI applies the
following rules **in order** and stops at the first that matches:

| # | Sentinel                       | Type     | tool_id source                                                  | Default command                                            |
| - | ------------------------------ | -------- | --------------------------------------------------------------- | ---------------------------------------------------------- |
| 0 | `executa.json`                 | (any)    | `tool_id` field (required)                                      | `command` field if set, else type-specific default below   |
| 1 | `pyproject.toml`               | `python` | first key of `[project.scripts]`                                | `["uv","run","--project",dir,tool_id]`                     |
| 2 | `package.json`                 | `node`   | `executa.tool_id` → first key of `bin` → `name`                 | `["node", <bin-entry-or-main-or-module>]`                  |
| 3 | `go.mod` (alone)               | `go`     | **error** — go.mod has no script-name field; supply `executa.json` | n/a                                                     |
| 4 | `bin/<dirname>` (executable)   | `binary` | the subdirectory name                                           | `[<absolute path to bin/<dirname>>]`                       |

If none of the above match, the subdirectory is **silently skipped** — this is
how `executas/<skill-name>/SKILL.md`-only folders coexist with launchable
plugins.

> ⚠️ **Duplicate `tool_id`** across multiple subdirectories: only the first
> registration is kept and the rest are skipped with a `⚠ skipping executa
> <name>: tool_id "..." already provided by <other>` warning. Use this to ship
> several language flavours of the same plugin and toggle the active one via
> the `enabled` field in each `executa.json`.

---

## 2 · `executa.json` schema

Place at `<manifest-dir>/executas/<name>/executa.json`. All fields except
`tool_id` and `type` are optional.

```jsonc
{
  // REQUIRED — the server-minted Tool ID. Must match MANIFEST.name in your
  // plugin source AND every reference in manifest.json + bundle/.
  "tool_id": "tool-yourhandle-foo-abcd1234",

  // REQUIRED — selects the default launch command and discovery semantics.
  "type": "python" | "node" | "go" | "binary",

  // OPTIONAL — explicit launch command. When present, fully overrides the
  // type-specific default. Always launched with cwd = this subdirectory.
  "command": ["uv", "run", "--reinstall", "."],

  // OPTIONAL — when false, the executa is excluded from auto-discovery.
  // Useful when you ship multiple language flavours of the same tool_id
  // and want to pick the active one declaratively (the others stay
  // alongside as documented alternates). Default: true.
  //
  // NOTE: an explicit `--executa dir=…` CLI flag bypasses this gate —
  // the user has already singled out one dir, so the auto-discovery dedup
  // rule does not apply.
  "enabled": true,

  // OPTIONAL — free-form annotation; ignored by the CLI but rendered nicely
  // in code review.
  "_comment": "..."
}
```

### Type-specific defaults

| `type`    | Default `command`                                                         | Notes                                                                           |
| --------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `python`  | `["uv","run","--project",<dir>,<tool_id>]`                                 | Requires `uv` 0.1+ on PATH. `<tool_id>` must be a `[project.scripts]` key.       |
| `node`    | `["node", <entry>]` where `<entry>` = `bin[tool_id]` → `bin` (string) → `main` → `module` | Requires `node` 18+ on PATH. No `npm install` is run by the harness. |
| `go`      | `["go","run","."]` (cwd = `<dir>`)                                         | Requires `go` 1.21+ on PATH. First launch compiles + caches; subsequent are fast. |
| `binary`  | `[<dir>/bin/<tool_id>]` if it exists; otherwise you MUST set `command`     | Plain executable, no Anna-side runtime needed.                                   |

---

## 3 · `--executa <spec>` CLI flag

Use this to register an executa that lives **outside** `executas/`, or to
override the auto-detected `command` for one run.

```bash
# Just point at a directory; the CLI applies the same rules as auto-discovery
# (executa.json → pyproject.toml → package.json → bin/<name>):
anna-app dev --executa dir=./vendor/external-tool

# Force a type even when no executa.json exists:
anna-app dev --executa dir=./executas/focus-session-go,type=go

# Fully explicit — ignore everything in the dir, just run this command:
anna-app dev --executa dir=./executas/foo,tool_id=tool-h-foo-12345678,command="node plugin.js"

# Repeatable; combine multiple plugins in one harness session:
anna-app dev \
  --executa dir=./executas/focus-session-node \
  --executa dir=./executas/extra-tool,type=binary
```

Spec syntax: comma-separated `key=value` pairs, all keys lowercase.

| Key       | Required               | Notes                                                                                              |
| --------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| `dir`     | always                 | Path to the executa subdir (absolute or relative to `--cwd`).                                      |
| `tool_id` | when overriding        | Skip if you only set `dir=` and want auto-detection.                                               |
| `type`    | for go without config  | One of `python | node | go | binary`.                                                              |
| `command` | when overriding        | Space-separated argv, OR a JSON array (use the JSON form when args contain spaces, e.g. `command='["node","--inspect","plugin.js"]'`). |

When `--executa` is used **at all**, it fully replaces auto-discovery for the
run; mix-and-match is intentional and explicit.

---

## 4 · End-to-end wiring

1. `anna-app dev` walks `executas/`, applies the discovery rules above, and
   builds a list of `{tool_id, project_dir, command}` triples.
2. The list is sent to the Python bridge over JSON-RPC:
   ```json
   { "method": "executas.register",
     "params": { "executas": [
       { "tool_id": "tool-...", "project_dir": "/abs/...", "command": ["..."] }
     ]}}
   ```
3. The bridge (`anna_app_runtime_local.executa.ExecutaPool`) records each
   spec in `_specs[tool_id] = ExecutaSpec(...)` but **does not spawn yet**.
4. When the bundle calls `anna.tools.invoke({tool_id, method, arguments})`,
   the bridge looks up the spec, lazy-spawns the subprocess with the supplied
   `command` (`cwd = project_dir`), opens stdio JSON-RPC, and forwards the
   `invoke` request.
5. Subsequent invocations reuse the already-running subprocess. The pool
   cleans up on harness shutdown.

For the in-process protocol the plugin must speak (`describe`, `invoke`,
`health` over newline-delimited JSON-RPC 2.0), see
[`docs/protocol-spec.md`](./protocol-spec.md). For tool_id minting and the
on-server registration flow, see the per-app README under `examples/`.

---

## Worked example

[`examples/anna-app-focus-flow/`](../examples/anna-app-focus-flow/) ships the
same Pomodoro tool implemented in all three languages:

```
executas/
├── focus-session-python/   executa.json: {type:"python", enabled: true}
├── focus-session-node/     executa.json: {type:"node",   enabled: false}
├── focus-session-go/       executa.json: {type:"go",     enabled: false}
└── focus-coach/            (Skill — SKILL.md only, not a launchable plugin)
```

By default `anna-app dev` launches the Python flavour. Toggle a different one
by flipping `enabled`, or run `anna-app dev --executa
dir=./executas/focus-session-node` to override for a single session
(`--executa dir=…` ignores `enabled: false` — the explicit flag wins).

All three plugins persist to the same `~/.anna/focus-flow/state.json` and
return identical InvokeResult envelopes — the bundle never knows which
runtime is active.
