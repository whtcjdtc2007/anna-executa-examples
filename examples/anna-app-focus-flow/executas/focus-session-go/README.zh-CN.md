# focus-session — Go 版本

`focus-session` 的 Go 实现，与 [`../focus-session-python`](../focus-session-python/)
行为完全一致：同一份 `~/.anna/focus-flow/state.json`、同一个 `session`
工具、同一组 `action` 取值。

English: [README.md](./README.md)

## 何时选用

- 需要单文件、零运行时依赖的二进制分发（参见下文「打成二进制」）；
- 想验证「同一个 Anna App bundle 可以无差别对接 Python / Node / Go executa」；
- 验证 `anna-app dev` 的 `go run` / `binary` 启动路径。

> ⚠️ 同一个 App 的 `manifest.json` 只声明了一个 `tool_id`，因此 **同时只能启用一种语言的 focus-session**。`anna-app dev` 在发现重复 `tool_id` 时会跳过后续实现并打印警告。

## 启用步骤（开发态：`go run`）

1. 在本目录的 [executa.json](./executa.json) 把 `enabled` 改为 `true`；
2. 把 [`../focus-session-python/executa.json`](../focus-session-python/executa.json) 的 `enabled` 改为 `false`；
3. 确保 `go version` ≥ 1.21；本插件仅用 Go 标准库，无需 `go get`；
4. 回到 App 根目录跑 `anna-app dev`。

`autoDiscoverExecutas` 默认启动命令等价于：

```bash
cd executas/focus-session-go && go run .
```

首次启动会触发一次编译；Go 会缓存编译产物，后续启动几乎是瞬时。

## 打成二进制 + 切换为 `binary` 模式

```bash
cd executas/focus-session-go
mkdir -p bin
go build -o bin/tool-test-focus-session-12345678 .
```

然后把 `executa.json` 改成：

```json
{
  "tool_id": "tool-test-focus-session-12345678",
  "type": "binary",
  "enabled": true
}
```

这时 `anna-app dev` 会直接启动 `bin/tool-test-focus-session-12345678`，
无需 `go` 工具链——这也是真实分发场景的形态，参见
[`anna-executa-examples/docs/binary-distribution.md`](../../../../docs/binary-distribution.md)。

也可以用 CLI flag 直接覆盖：

```bash
anna-app dev --executa dir=./executas/focus-session-go,type=binary
# or
anna-app dev --executa dir=./executas/focus-session-go,type=go
```

## 协议契约

- 单 tool：`session(action, duration_minutes?, topic?, notes?)`
- `action ∈ {start, pause, resume, complete, get_state}`
- 返回 InvokeResult 信封 `{success, data}` / `{success:false, error}`
- 状态文件路径与 Python / Node 版完全相同；切换语言不会丢失历史。

完整 API 与设计取舍参见 [`../focus-session-python/README.zh-CN.md`](../focus-session-python/README.zh-CN.md)。
