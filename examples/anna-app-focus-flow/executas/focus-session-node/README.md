# focus-session — Node.js flavour

Node.js implementation of `focus-session`. Behaviour is identical to
[`../focus-session-python`](../focus-session-python/): same
`~/.anna/focus-flow/state.json`, same `session` tool, same `action`
values.

中文版：[README.zh-CN.md](./README.zh-CN.md)

## When to choose this flavour

- You already have a Node toolchain and don't want to introduce
  `uv` / Python.
- You want to verify that "the same Anna App bundle works seamlessly
  against Python / Node / Go executas".
- Cross-runtime compatibility regression in CI.

> ⚠️ The App's `manifest.json` declares only **one** `tool_id`, so only
> **one** language flavour of `focus-session` can be enabled at a time.
> When `anna-app dev` discovers a duplicate `tool_id` it skips the later
> implementation and prints a warning.

## Enabling this flavour

1. In this directory's [executa.json](./executa.json) flip `enabled` to `true`.
2. In [`../focus-session-python/executa.json`](../focus-session-python/executa.json) flip `enabled` to `false`.
3. Make sure `node --version` ≥ 18. This plugin only uses Node built-in
   modules, so no `npm install` is required.
4. Back at the App root, run `anna-app dev`.

## Default launch command

`autoDiscoverExecutas` parses [package.json](./package.json)'s `bin`
field, which is equivalent to:

```bash
node ./focus_session_plugin.js
```

You can also override it explicitly via `executa.json`'s
`"command": ["node","focus_session_plugin.js"]`, or via the CLI flag:

```bash
anna-app dev --executa dir=./executas/focus-session-node,type=node
```

## Protocol contract

- Single tool: `session(action, duration_minutes?, topic?, notes?)`
- `action ∈ {start, pause, resume, complete, get_state}`
- Returns the InvokeResult envelope `{success, data}` / `{success:false, error}`
- State file path is identical to the Python flavour — switching
  languages does not lose history.

For the full API and design trade-offs see
[`../focus-session-python/README.md`](../focus-session-python/README.md).
