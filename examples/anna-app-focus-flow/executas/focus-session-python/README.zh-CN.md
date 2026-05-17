# focus-session

**Focus Flow** Anna App 使用的 Executa stdio Tool 插件。

| 字段             | 值                                                              |
| ---------------- | --------------------------------------------------------------- |
| 插件名           | 服务器 Mint 出的 `tool_id`（如 `tool-yourhandle-focus-session-abcd1234`） |
| Executa `tool_id`| 同上 —— 在 <https://anna.partners/executa> Mint                  |
| 分发方式         | `uv tool install .`（或 `pipx`、打包二进制）                     |
| 入口             | Mint 出的 ID（写入 `pyproject.toml` 的 `[project].name`）         |
| 持久化状态       | `~/.anna/focus-flow/state.json`                                 |
| 运行时依赖       | 无 —— 仅使用 Python 标准库                                       |

## Tool 表面 —— 单 dispatcher 方法

插件暴露 **一个** tool 方法（`session`），通过 `action` 参数选择行为。
bundle 调用 `anna.tools.invoke({ tool_id: "<minted>", method: "session", args: {...} })`；
dispatcher 把整个 Mint 的 `tool_id` 作为 NATS 插件名，把 `method` 作为插件内 tool 名。

| `action`    | 必填参数                       | 返回                                  |
| ----------- | ------------------------------ | ------------------------------------- |
| `start`     | `duration_minutes`、`topic?`   | `{ active }`                          |
| `pause`     | —                              | `{ active }`                          |
| `resume`    | —                              | `{ active }`                          |
| `complete`  | `notes?`                       | `{ completed, today }`                |
| `get_state` | —                              | `{ active, today, recent[<=10] }`     |

## JSON-RPC 协议（stdio）

插件在 stdin/stdout 上实现三个 JSON-RPC 2.0 方法：

| 方法       | 参数                                            | 返回                  |
| ---------- | ----------------------------------------------- | --------------------- |
| `describe` | `{}`                                            | 插件 manifest         |
| `invoke`   | `{ "tool": "session", "arguments": {...} }`     | tool 返回值           |
| `health`   | `{}`                                            | `{ "status": "ok" }`  |

`invoke` 的 `arguments` 与上表一致。

## 本地运行

```bash
uv tool install .
focus-session   # 在 stdin 上等待 JSON-RPC 请求

# 冒烟测试：
echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' | focus-session
```

多次调用：

```bash
focus-session <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"describe"}
{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"tool":"session","arguments":{"action":"start","duration_minutes":1,"topic":"smoke"}}}
{"jsonrpc":"2.0","id":3,"method":"invoke","params":{"tool":"session","arguments":{"action":"get_state"}}}
{"jsonrpc":"2.0","id":4,"method":"invoke","params":{"tool":"session","arguments":{"action":"complete","notes":"ok"}}}
{"jsonrpc":"2.0","id":5,"method":"invoke","params":{"tool":"unknown","arguments":{}}}
EOF
```

第五次调用应返回 JSON-RPC 错误（unknown tool）。

本插件如何嵌入整个 Anna App，包括 **🪪 Mint** 步骤（保留服务器侧的
`tool_id`，并把它写到 `pyproject.toml` 的 `[project].name` 与插件
`describe` 返回的 manifest 中），参见 [`../../README.zh-CN.md`](../../README.zh-CN.md)。
