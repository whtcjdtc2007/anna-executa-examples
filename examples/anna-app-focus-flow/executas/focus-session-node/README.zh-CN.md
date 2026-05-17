# focus-session — Node.js 版本

`focus-session` 的 Node.js 实现，与 [`../focus-session-python`](../focus-session-python/)
行为完全一致：同一份 `~/.anna/focus-flow/state.json`、同一个 `session`
工具、同一组 `action` 取值。

English: [README.md](./README.md)

## 何时选用

- 团队已有 Node 工具链，不想引入 `uv` / Python；
- 想验证「同一个 Anna App bundle 可以无差别对接 Python / Node / Go executa」；
- CI 上做 cross-runtime 兼容性回归。

> ⚠️ 同一个 App 的 `manifest.json` 只声明了一个 `tool_id`，因此 **同时只能启用一种语言的 focus-session**。`anna-app dev` 在发现重复 `tool_id` 时会跳过后续实现并打印警告。

## 启用步骤

1. 在本目录的 [executa.json](./executa.json) 把 `enabled` 改为 `true`；
2. 把 [`../focus-session-python/executa.json`](../focus-session-python/executa.json) 的 `enabled` 改为 `false`；
3. 确保 `node --version` ≥ 18；本插件只用 Node 内置模块，无需 `npm install`；
4. 回到 App 根目录跑 `anna-app dev`。

## 默认启动命令

`autoDiscoverExecutas` 解析 [package.json](./package.json) 的 `bin` 字段，等价于：

```bash
node ./focus_session_plugin.js
```

也可以在 `executa.json` 里显式覆盖 `"command": ["node","focus_session_plugin.js"]`，或用 CLI flag：

```bash
anna-app dev --executa dir=./executas/focus-session-node,type=node
```

## 协议契约

- 单 tool：`session(action, duration_minutes?, topic?, notes?)`
- `action ∈ {start, pause, resume, complete, get_state}`
- 返回 InvokeResult 信封 `{success, data}` / `{success:false, error}`
- 状态文件路径与 Python 版完全相同；切换语言不会丢失历史。

完整 API 与设计取舍参见 [`../focus-session-python/README.zh-CN.md`](../focus-session-python/README.zh-CN.md)。
