# 多语言 Anna App — `executa.json` 与发现机制

`anna-app dev`（[`@anna-ai/cli`][cli] 提供的本地 harness CLI）会用真正的
`anna-app-runtime-local` Python bridge 跑你的 Anna App，并在第一次
`tools.invoke` 时按需懒启动每个 Tool 插件（Executa）的子进程。
从 v0.1.10 起，发现层做到了 **语言无关**：同一个 App 可以在
`executas/<name>/` 下混搭 Python、Node.js、Go 或预编译二进制 executa，
每个 executa 都按照自己声明的方式被拉起。

本文档说明：

1. `anna-app dev` 对 `<manifest-dir>/executas/` 子目录的发现规则；
2. `executa.json` 配置文件 schema（推荐的显式声明方式）；
3. `--executa <spec>` CLI flag 的一次性 / 跨目录用法；
4. `command` 字段如何贯穿 CLI → bridge → 实际进程。

[cli]: https://www.npmjs.com/package/@anna-ai/cli

---

## 1 · 发现顺序

CLI 依次按下表规则匹配 `<manifest-dir>/executas/` 下的每个子目录，
**遇到第一个匹配项即停**：

| 序号 | Sentinel                       | type     | tool_id 来源                                                  | 默认启动命令                                                |
| ---- | ------------------------------ | -------- | ------------------------------------------------------------- | ---------------------------------------------------------- |
| 0    | `executa.json`                 | （任意） | `tool_id` 字段（必填）                                         | `command` 字段；缺省时按下方 type 默认                       |
| 1    | `pyproject.toml`               | `python` | `[project.scripts]` 的第一个 key                                | `["uv","run","--project",dir,tool_id]`                     |
| 2    | `package.json`                 | `node`   | `executa.tool_id` → `bin` 的第一个 key → `name`                | `["node", <bin-entry-or-main-or-module>]`                  |
| 3    | 仅有 `go.mod`                  | `go`     | **报错** — go.mod 没有 script 概念，必须配 `executa.json`        | n/a                                                        |
| 4    | `bin/<dirname>`（可执行）       | `binary` | 子目录名                                                       | `[<bin/<dirname> 的绝对路径>]`                              |

任何都不匹配的子目录会被 **静默忽略** —— 这就是
`executas/<skill-name>/SKILL.md` 这类「只是文档」的目录可以与可启动插件
共存的原因。

> ⚠️ **同一 `tool_id` 出现在多个子目录**：只保留第一个，其余跳过并打印
> `⚠ skipping executa <name>: tool_id "..." already provided by <other>`。
> 这正是「同一个插件提供 Python / Node / Go 三种实现，通过 `enabled` 切换」
> 这种用法的工作机制。

---

## 2 · `executa.json` 字段

放在 `<manifest-dir>/executas/<name>/executa.json`。除 `tool_id`、`type` 外都可选。

```jsonc
{
  // 必填 —— 服务器 Mint 出的 Tool ID。必须与插件源码里的 MANIFEST.name
  // 以及 manifest.json + bundle/ 中所有引用完全一致。
  "tool_id": "tool-yourhandle-foo-abcd1234",

  // 必填 —— 决定默认启动命令与发现语义。
  "type": "python" | "node" | "go" | "binary",

  // 可选 —— 显式启动命令，会完全覆盖 type 默认值。cwd 始终是本子目录。
  "command": ["uv", "run", "--reinstall", "."],

  // 可选 —— false 时会从自动发现里排除。适合「同一个 tool_id 提供多语言
  // 实现，只有一个生效」的场景。默认 true。
  //
  // 注：显式的 `--executa dir=…` CLI flag 会绕过这个开关 —— 用户已经
  // 明确指定了这个目录，自动发现的去重规则不适用。
  "enabled": true,

  // 可选 —— 自由备注；CLI 不读取，但在 review / git diff 里能给读者上下文。
  "_comment": "..."
}
```

### 各 type 的默认命令

| `type`    | 默认 `command`                                                            | 备注                                                                            |
| --------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `python`  | `["uv","run","--project",<dir>,<tool_id>]`                                 | 需要 PATH 上有 `uv` 0.1+。`<tool_id>` 必须是 `[project.scripts]` 的 key。        |
| `node`    | `["node", <entry>]`，`<entry>` = `bin[tool_id]` → `bin`（字符串）→ `main` → `module` | 需要 `node` 18+。harness 不会替你执行 `npm install`。                  |
| `go`      | `["go","run","."]`（cwd = `<dir>`）                                         | 需要 `go` 1.21+。首次启动会编译 + 缓存，后续启动几乎瞬时。                       |
| `binary`  | 若 `<dir>/bin/<tool_id>` 存在则用它；否则你必须显式给 `command`              | 普通可执行文件，不依赖任何 Anna 侧运行时。                                       |

---

## 3 · `--executa <spec>` CLI flag

用于注册 **不在 `executas/` 下** 的 executa，或为某次运行临时覆盖
自动检测出的 `command`。

```bash
# 只给 dir=：和自动发现走完全一样的规则
# （executa.json → pyproject.toml → package.json → bin/<name>）：
anna-app dev --executa dir=./vendor/external-tool

# 在没有 executa.json 的情况下强行指定 type：
anna-app dev --executa dir=./executas/focus-session-go,type=go

# 完全显式 —— 忽略目录里的所有 sentinel，就跑这条命令：
anna-app dev --executa dir=./executas/foo,tool_id=tool-h-foo-12345678,command="node plugin.js"

# 可重复使用，把多个插件塞进同一次 harness：
anna-app dev \
  --executa dir=./executas/focus-session-node \
  --executa dir=./executas/extra-tool,type=binary
```

Spec 语法：逗号分隔的 `key=value`，所有 key 小写。

| Key       | 何时必填                  | 说明                                                                                              |
| --------- | ------------------------- | ------------------------------------------------------------------------------------------------- |
| `dir`     | 始终                      | executa 目录路径（绝对或相对 `--cwd`）。                                                          |
| `tool_id` | 想覆盖自动检测时           | 只给 `dir=` 想走自动检测时可省略。                                                                |
| `type`    | go 又没 `executa.json` 时  | `python | node | go | binary` 之一。                                                              |
| `command` | 想覆盖默认命令时           | 用空格分隔的 argv，或 JSON 数组（参数里有空格时用 JSON：`command='["node","--inspect","plugin.js"]'`）。 |

只要用了 `--executa`，本次运行就 **完全替换** 自动发现 —— 不会两路混跑。

---

## 4 · 端到端链路

1. `anna-app dev` 走完 `executas/` 的发现规则，得到一组
   `{tool_id, project_dir, command}` 三元组；
2. 通过 JSON-RPC 发给 Python bridge：
   ```json
   { "method": "executas.register",
     "params": { "executas": [
       { "tool_id": "tool-...", "project_dir": "/abs/...", "command": ["..."] }
     ]}}
   ```
3. bridge（`anna_app_runtime_local.executa.ExecutaPool`）把每条 spec 记到
   `_specs[tool_id] = ExecutaSpec(...)`，**此时还不会拉起进程**；
4. 当 bundle 发起 `anna.tools.invoke({tool_id, method, arguments})` 时，
   bridge 查到 spec，按你给的 `command` 懒启动子进程
   （`cwd = project_dir`），打开 stdio JSON-RPC，转发 `invoke` 请求；
5. 后续调用复用同一进程，harness 关闭时统一回收。

插件本身需要遵守的进程内协议（newline-delimited JSON-RPC 2.0 上的
`describe` / `invoke` / `health`）参见
[`docs/protocol-spec.zh-CN.md`](./protocol-spec.zh-CN.md)。tool_id 的
Mint 与服务器侧注册流程参见 `examples/` 下各 App 的 README。

---

## 工作示例

[`examples/anna-app-focus-flow/`](../examples/anna-app-focus-flow/) 用三种语言
实现了同一个 Pomodoro 工具：

```
executas/
├── focus-session-python/   executa.json: {type:"python", enabled: true}
├── focus-session-node/     executa.json: {type:"node",   enabled: false}
├── focus-session-go/       executa.json: {type:"go",     enabled: false}
└── focus-coach/            （Skill — 只有 SKILL.md，不会被启动）
```

默认情况下 `anna-app dev` 拉起 Python 版。想换实现就翻转对应的
`enabled`；或者在某次运行里直接 `anna-app dev --executa
dir=./executas/focus-session-node` 一次性覆盖（`--executa dir=…`
会忽略 `enabled: false` —— 显式 flag 优先）。

三种实现都把状态写到同一份 `~/.anna/focus-flow/state.json`、返回
完全一致的 InvokeResult 信封 —— bundle 永远不需要知道后端跑的是哪种语言。
