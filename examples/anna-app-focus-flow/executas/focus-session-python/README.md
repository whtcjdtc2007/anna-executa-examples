# focus-session

Executa stdio Tool plugin used by the **Focus Flow** Anna App.

中文版：[README.zh-CN.md](./README.zh-CN.md)

| Field            | Value                                                |
| ---------------- | ---------------------------------------------------- |
| Plugin name      | server-minted `tool_id` (e.g. `tool-yourhandle-focus-session-abcd1234`) |
| Executa `tool_id`| same minted string — mint at <https://anna.partners/executa> |
| Distribution     | `uv tool install .` (or `pipx`, vendored binary)     |
| Entry point      | the minted ID (set as `[project].name` in `pyproject.toml`) |
| Persistent state | `~/.anna/focus-flow/state.json`                      |
| Runtime deps     | none — Python stdlib only                            |

## Tool surface — single dispatcher method

The plugin exposes **one** tool method (`session`). Behavior is selected by
the `action` argument. The bundle calls
`anna.tools.invoke({ tool_id: "<minted>", method: "session", args: {...} })`;
the dispatcher uses the entire minted `tool_id` as the NATS plugin name and
`method` as the in-plugin tool name.

| `action`     | Required args                | Returns                              |
| ------------ | ---------------------------- | ------------------------------------ |
| `start`      | `duration_minutes`, `topic?` | `{ active }`                         |
| `pause`      | —                            | `{ active }`                         |
| `resume`     | —                            | `{ active }`                         |
| `complete`   | `notes?`                     | `{ completed, today }`               |
| `get_state`  | —                            | `{ active, today, recent[<=10] }`    |

## JSON-RPC protocol (stdio)

The plugin implements three JSON-RPC 2.0 methods on stdin/stdout:

| Method     | Params                                          | Result                |
| ---------- | ----------------------------------------------- | --------------------- |
| `describe` | `{}`                                            | the plugin manifest   |
| `invoke`   | `{ "tool": "session", "arguments": {...} }`     | tool return value     |
| `health`   | `{}`                                            | `{ "status": "ok" }`  |

`invoke` `arguments` mirror the table above.

## Run locally

```bash
uv tool install .
focus-session   # waits for JSON-RPC requests on stdin

# Smoke test:
echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' | focus-session
```

Multi-call test:

```bash
focus-session <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"describe"}
{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"tool":"session","arguments":{"action":"start","duration_minutes":1,"topic":"smoke"}}}
{"jsonrpc":"2.0","id":3,"method":"invoke","params":{"tool":"session","arguments":{"action":"get_state"}}}
{"jsonrpc":"2.0","id":4,"method":"invoke","params":{"tool":"session","arguments":{"action":"complete","notes":"ok"}}}
{"jsonrpc":"2.0","id":5,"method":"invoke","params":{"tool":"unknown","arguments":{}}}
EOF
```

The fifth call should return a JSON-RPC error (unknown tool).

See [`../../README.md`](../../README.md) for how this plugin slots into the
overall Anna App, including the **🪪 Mint** step that reserves the
server-controlled `tool_id` (you then bake it into both `pyproject.toml`'s
`[project].name` and the manifest the plugin returns from `describe`).
