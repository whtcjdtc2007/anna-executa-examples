# Focus Flow — Anna App 示例

> 番茄钟 / 深度工作计时器，打包为可安装的 **Anna App**。包含一个 stdio Tool 插件
> （`focus-session`）、一个教练 Skill（`focus-coach`），以及一个在 Anna UI Runtime
> 沙箱中运行的精致 UI bundle。
>
> 同一个插件提供了 **三份实现** — Python、Node.js、Go，遵循同一份
> JSON-RPC 契约，方便你按团队技术栈选一种，也能验证 harness
> 对三种运行时都能走通。

[English](./README.md)

---

## 选择语言

App 的 UI bundle、manifest 与 Skill 是语言无关的。`focus-session` Tool 插件
提供三种口味，由各自 `executa.json` 里的 `enabled` 字段决定哪一种被
`anna-app dev` 拉起：

| 实现    | 目录                                                                  | 默认 `enabled` | 运行要求 |
| ------- | --------------------------------------------------------------------- | --------------- | ------- |
| Python  | [`executas/focus-session-python/`](./executas/focus-session-python/)  | `true`  | PATH 上有 `uv` 0.1+ |
| Node.js | [`executas/focus-session-node/`](./executas/focus-session-node/)      | `false` | PATH 上有 `node` 18+（无需 `npm install`） |
| Go      | [`executas/focus-session-go/`](./executas/focus-session-go/)          | `false` | `go` 1.21+（或预构建二进制） |

三者共享同一份 `~/.anna/focus-flow/state.json`、同一组 `session` tool 接口、
同一种 InvokeResult 信封 —— bundle 永远不需要知道后端跑的是哪种语言。切换
实现只需把对应 `executa.json` 的 `enabled` 改成 `true`，其它两份改成 `false`
（同一个时刻只能启用一种；`anna-app dev` 会对重复 `tool_id` 告警）。

也可以不改 `enabled`，直接用命令行 flag 指定本次要跑哪种 —— `--executa`
会覆盖 `enabled: false`：

```bash
anna-app dev --executa dir=./executas/focus-session-node
anna-app dev --executa dir=./executas/focus-session-go,type=go
```

完整发现规则与 `executa.json` schema 参见
[`docs/multi-language-anna-apps.md`](../../docs/multi-language-anna-apps.md)。

---

## 目录结构

```
anna-app-focus-flow/
├── app.json                          # App 元数据（slug、name、category…）
├── manifest.json                     # AppManifest（schema:2）
├── scripts/
│   └── set-tool-id.py                # 一键把 Mint 出的 ID 写进 / 重置回 Python flavour 的文件
├── bundle/                           # UI Runtime 加载的 static-spa
│   ├── index.html
│   ├── style.css
│   ├── app.js                        # 调用 anna.* RPC SDK
│   └── icon.svg
└── executas/
    ├── focus-session-python/         # stdio Tool 插件 — Python / uv（默认）
    │   ├── executa.json              #   {tool_id, type:"python", enabled:true}
    │   ├── pyproject.toml
    │   ├── focus_session_plugin.py
    │   └── README.md
    ├── focus-session-node/           # stdio Tool 插件 — Node.js 18+
    │   ├── executa.json              #   {tool_id, type:"node", enabled:false}
    │   ├── package.json
    │   ├── focus_session_plugin.js
    │   └── README.md
    ├── focus-session-go/             # stdio Tool 插件 — Go 1.21+
    │   ├── executa.json              #   {tool_id, type:"go", enabled:false}
    │   ├── go.mod
    │   ├── main.go
    │   └── README.md
    └── focus-coach/
        └── SKILL.md                  # 声明式 Skill（YAML frontmatter）
```

## 三方协作

```
┌──────────────┐    anna.tools.invoke    ┌──────────────────────┐
│ bundle/app.js│ ──────────────────────▶ │ Anna UI Runtime      │
│  （沙箱）     │ ◀─────────────────────  │   ↳ host dispatcher  │
└──────────────┘    JSON-RPC 结果        └──────────┬───────────┘
                                                    │ NATS
                                                    ▼
                                       ┌───────────────────────────────┐
                                       │ executas/focus-session-{python, │
                                       │   node, go} — 选一种启用         │
                                       │   （stdio 插件）                  │
                                       └───────────────────────────────┘
```

每当此 App 窗口获得焦点时，Anna 会把 Skill（`focus-coach`）注入到 LLM 的 system
prompt，由它告诉模型何时 / 如何调用工具。

---

## Tool 表面 — 单分发方法

`focus-session` 插件只暴露 **一个** 工具方法 `session`，通过 `action` 参数区分行为。
Anna 中一个 Executa 对应一个运行中的插件（通过服务器 Mint 出的 `tool_id`
匹配），bundle 再通过 `tools.invoke` 的 `method` 参数选择插件内部
要调用的方法。插件收敛到单一分发方法后，每个 App 就只需一行
Executa，bundle 只需在 `action` 上切换。

| `action`    | 参数                          | 返回                                          |
| ----------- | ----------------------------- | --------------------------------------------- |
| `start`     | `duration_minutes`, `topic?`  | `{ active }`                                  |
| `pause`     | —                             | `{ active }`                                  |
| `resume`    | —                             | `{ active }`                                  |
| `complete`  | `notes?`                      | `{ completed, today }`                        |
| `get_state` | —                             | `{ active, today, recent }`                   |

状态持久化到 `~/.anna/focus-flow/state.json`。stdio JSON-RPC 协议详见
[executas/focus-session-python/README.md](./executas/focus-session-python/README.md)。

---

## AppManifest 关键字段

`manifest.json` 由 matrix-nexus 中 [`AppManifest`][schema] Pydantic 模型
（`extra="forbid"`）+ 静态 UI 校验器共同验证：

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

> **先 Mint 你自己的 ID。** 上面两个 `tool_id` 都是占位符。
> 在 <https://anna.partners/executa>（*My Tools* / *My Skills* →
> **Create** → **🪪 Mint**）分别 Mint 出 Tool / Skill 的服务器 ID，
> 再把 Mint 后的字符串填入 `manifest.json`（`required_executas` +
> `host_api.tools`）和 `bundle/app.js`（`TOOL_ID`）。

### Tool ID & ACL 不变量

依据 `matrix-nexus/src/services/anna_app_rpc_dispatcher.py` 验证：

- **仅 Mint 生成 `tool_id`。** Anna 仅在服务器端用
  `tool-{handle}-{slug}-{uniq}` / `skill-{handle}-{slug}-{uniq}` 格式
  生成 `tool_id`，客户端无法选择或覆盖。`required_executas[].tool_id`
  必须是 Mint 出的字符串。
- bundle 以
  `anna.tools.invoke({ tool_id, method, args })` 调用工具。dispatcher
  把整个 `tool_id` 作为 NATS 插件名、`method` 作为插件内部的
  工具名路由。如果未提供 `method`，后端会回退到旧的
  `tool_id="plugin.method"` 划分規则，作为向后兼容。
- `_is_tool_allowed` 在 `tool_id` 与 `required_executas[].tool_id` /
  `optional_executas[].tool_id` 之间做 **字面相等** 比较（`required:*` /
  `optional:*` 通配符除外）。**没有** plugin 名前缀匹配。

`host_api.tools` 接受 `["required:*"]`、`["optional:*"]`、`"required:<id>"`、
`"optional:<id>"` 或裸 `"<id>"`——裸形与前缀形都必须出现在
`required_executas` / `optional_executas` 中。

### `host_api` 真实表面（依据 dispatcher 派发表）

| 命名空间   | 方法                                                                     |
| ---------- | ------------------------------------------------------------------------ |
| `tools`    | `list`、`invoke({ tool_id, method, args })`                              |
| `chat`     | `write_message({ role, content })`、`append_artifact`、`read_history`    |
| `storage`  | `get({ key })`、`set({ key, value })`、`delete({ key })`                 |
| `window`   | `hello`、`ready`、`set_title({title})`、`resize({w,h})`、`focus`、`close({reason})`、`open_view({view, payload})`、`report_error` |
| `artifact`、`llm`、`fs`、`prefs` | 当前为占位（`not_implemented`）                  |

注意：

- `window.hello`、`window.ready`、`window.report_error` 即使不写在
  `host_api.window` 中也会自动放行（位于 `_NO_AUTH_NEEDED`）。
- 整个 `window` 命名空间被特例放行，不论 `host_api.window` 列表是什么——
  在那里列出方法只是**展示性**的。
- manifest 根的 `permissions[]` 是自由文本，仅用于展示 / 审计；
  **运行时 ACL 由 `host_api.*` 强制执行**。但校验器会限制
  `permissions[]` 只能使用已知词汇——这里要用
  `storage.read` / `storage.write`（不是 `storage.get` / `storage.set`），
  尽管运行时实际的方法名是 `storage.get` / `storage.set` /
  `storage.delete`。

---

## bundle/app.js 中实际使用的 SDK 调用

bundle 从 `/static/anna-apps/_sdk/0.1.0/index.js` 加载运行时 SDK（全局：
`AnnaAppRuntime`），通过以下方式连接：

```js
const anna = await AnnaAppRuntime.connect();
// ↑ 需要 URL 参数 `wid` 与 `t`，Anna 打开 iframe 时会自动注入。
//   独立预览会抛错，bundle 回退到独立模式。
```

| 目的                  | 真实 SDK 调用                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| 调用工具              | `anna.tools.invoke({ tool_id: "<minted>", method: "session", args: {...} })`                   |
| 读取 storage          | `const { value } = await anna.storage.get({ key })`                                            |
| 写入 storage          | `await anna.storage.set({ key, value })`                                                       |
| 追加聊天消息          | `await anna.chat.write_message({ role: "user", content: "..." })`                              |
| 更新窗口标题          | `await anna.window.set_title({ title })`                                                       |
| 通知 ready            | `AnnaAppRuntime.connect()` 已自动发送 — 无需手工调用                                              |

注意：**没有** `window.set_summary`，**没有** `storage.read/write`，
`tools.invoke` 的 envelope 也不是 `{tool, method: ..., arguments}`——上述写法
都会被 dispatcher 拒绝。（上面表格里的 `method` 是 **插件** 方法名，不是
JSON-RPC envelope 的 key。）

---

## 安装 — Tool 插件（`focus-session`）

插件是基于 stdio 的 Executa，使用 JSON-RPC 通信。推荐通过 `uv tool install`
分发（Anna 同时支持 `pipx` / `binary` / `local`）。Mint 出的 Tool ID
必须**完全一致地**出现在 4 个位置：`pyproject.toml` 的 `[project].name`
与 `[project.scripts]` key、插件的 `MANIFEST["name"]`、`manifest.json`
（`required_executas[].tool_id` + `ui.host_api.tools`）、以及
`bundle/app.js` 的 `TOOL_ID`。仓库里默认是 `*-CHANGEME-*` 占位符，并附带
一个脚本一次性同步这 4 处。

```bash
cd executas/focus-session-python
# 1) 占位符冒烟测试（此时还不需要 Mint ID）：
uv tool install . --reinstall
echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' \
    | tool-CHANGEME-focus-session-CHANGEME
uv tool uninstall tool-CHANGEME-focus-session-CHANGEME    # 清理
```

随后在 [anna.partners/executa](https://anna.partners/executa) 注册为 Executa：

1. 进入 **My Tools** 标签页 → 点击 **Create Tool**，选择类型 `tool`，
   填一个易读的 *display name*（如 `Focus Session`）。表单的 *Name* 字段
   仅作展示——真正的路由身份是 Mint 出的 Tool ID，与这里填什么无关。
2. 点击 Tool ID 旁的 **🪪 Mint** 按钮。
   - Anna 会生成形如 `tool-{your-handle}-focus-session-{uniq}` 的
     服务器控制 ID 并锁定到当前账号（草稿 24 小时内有效）。
   - **你不能手动输入 ID**。该输入框为只读，在 Mint 成功之前 **Create**
     按钮始终禁用；客户端提交的任何 `tool_id` 字段都会被服务器静默丢弃。
3. 用脚本把 Mint 出的 ID 一次性写入 4 个文件：

   ```bash
   # 在示例根目录下：
   scripts/set-tool-id.py apply --tool tool-yourhandle-focus-session-abcd1234
   scripts/set-tool-id.py status   # 校对一下
   ```

4. 用新名字重装插件，确认 shim 可以被 PATH 解析：

   ```bash
   cd executas/focus-session-python
   uv tool install . --reinstall --no-cache
   which tool-yourhandle-focus-session-abcd1234   # → ~/.local/bin/<minted>
   echo '{"jsonrpc":"2.0","id":1,"method":"describe"}' \
       | tool-yourhandle-focus-session-abcd1234
   ```

   `describe` 返回的 `manifest.name` 必须等于 Mint 出的 `tool_id`，
   否则 Agent 会显示 Stopped 卡片，bundle 的 `tools.invoke` 也无法路由。
   （`set-tool-id.py` 已经替你改了 `MANIFEST["name"]`。）

5. 回到表单，*Distribution* 区按你**实际使用的安装方式**填：

   | Distribution | `package_name` 含义                | 何时使用                                     |
   | ------------ | ---------------------------------- | -------------------------------------------- |
   | `uv`         | uv 安装规格（这里就是 Mint ID）    | 上面用 `uv tool install .` 装的。**推荐。**   |
   | `pipx`       | pipx 安装规格（Mint ID）           | 改用 `pipx install .` 时选这个。              |
   | `binary`     | 不用，由 `binary_urls` 提供 per-OS | 你为各 OS 上传了下载 archive 时使用。         |
   | `local`      | Agent 上 archive 的**绝对路径**    | Air-gapped / 自测；archive 已经在 Agent 上。 |

   走 `uv` 这条路时，`package_name` 与 `executable_name` **都**填 Mint
   出的 ID。matrix-nexus 在 Agent 上会用 `shutil.which(<executable_name>)`
   定位可执行文件，所以它必须等于 `[project.scripts]` 的 entry point 名。

6. 粘贴 `describe` 返回的 manifest（matrix-nexus 也会在保存后重新拉取
   并缓存），点击 **Create** 提交草稿。

> **修改插件代码后重装：** `uv tool install --reinstall .`
> （仅 `uv tool install .` 在版本号未变时会跳过更新，旧字节码仍会被使用）。

> **提交前重置示例仓库：** 跑 `scripts/set-tool-id.py reset`，把 4 个
> 文件全部还原成 `*-CHANGEME-*` 占位符，避免把个人 Mint ID 推到上游。

## 安装 — Skill（`focus-coach`）

1. 在 <https://anna.partners/executa> 打开 **My Skills** → **Create Skill**。
2. 填入名称（如 `focus-coach`），选择类型 `skill`，点击 **🪪 Mint**
   预留服务器 ID（形如 `skill-{your-handle}-focus-coach-{uniq}`）。
3. 上传或粘贴 `executas/focus-coach/SKILL.md` 作为 Skill 内容，点击 **Create**。
4. 将 Mint 出的 Skill ID 填入 `manifest.json`（`required_executas`）。

## 安装 — Anna App

1. <https://anna.partners/executa> → **My Apps** → **Create App**。
2. 在 **Listing** 标签页填入 `app.json` 字段（slug = `focus-flow`，
   category = `productivity`）并保存。
3. 在 **Versions** 标签页创建版本：
   - 点 *Create*，把 `manifest.json` 的内容粘进 manifest 文本框，填写
     版本号后提交。manifest 是以 JSON 对象提交的，不需要单独的文件上传。
   - 在新版本行点 **Bundle**，通过 Bundle 上传器把 `bundle/` 目录下的
     所有文件（`index.html`、`app.js`、`style.css`、`icon.svg`…）逐一
     上传。文件会被直传到对象存储，并由服务端最终化。
   - 确保 manifest 中每个 `required_executas[].tool_id` 与上面 Mint 出的
     Tool / Skill ID **字面完全一致**（运行时 dispatcher 做严格字符串
     相等比较）。
4. matrix-nexus 对 manifest 跑三层校验：
   - `AppManifest`（Pydantic v2，`extra="forbid"`）校验结构与类型。
   - `validate_ui_section_static` 校验 CSP、view 几何，以及
     `host_api.tools` 中的每一项必须落在 `required_executas` /
     `optional_executas` 中。
   - 数据库校验：被引用的 Executa 必须存在，且可见性允许被 App 打包
     （`app_bundled` 或 `public`）。
5. **提交审核** → 等管理员 *Approve* → **Publish** 该版本 → 在 App
   详情页 **Install** → 在侧边栏打开。App 未达到 `APPROVED`
   （或已经 `PUBLISHED`）状态前，发布会被拒绝。

只有当用户账户已经安装了两个 Executa（Mint 出的 Tool 与 Skill ID），
App 安装才会成功——Anna 会拒绝缺失 `required_executas` 的安装请求。

---

## 本地开发

推荐的开发循环使用 [`@anna-ai/cli`](https://www.npmjs.com/package/@anna-ai/cli)
（已作为 devDependency 写入本示例的 `package.json`）。它会在
`http://localhost:5180` 启动 harness，自动发现 `executas/focus-session-python/`
下的 Executa，并把 `anna.*` RPC 转发给真实的 Python bridge
（`anna-app-runtime-local`）—— 与 Anna 生产环境**完全相同**的 host 表面。

### 1. 一次性环境准备

```bash
# 仓库根目录
pnpm install                # 为所有示例安装 @anna-ai/cli
uv --version                # 启动 bridge / executa 必需
anna-app doctor             # 检查 uv、runtime 版本钉等
```

> 如果 `which anna-app` 找不到，请用 `pnpm` 间接调用（如
> `pnpm --filter anna-app-focus-flow dev`），这样本地的 CLI 二进制
> 才会在 PATH 上。

### 2. 启动 dev harness

```bash
cd examples/anna-app-focus-flow
pnpm dev                    # → anna-app dev
# Harness UI：        http://localhost:5180
# RPC 日志面板：       harness 窗口右侧
# Bundle 热重载：      修改 bundle/ 下文件会自动 reload
```

首次 `tools.invoke` 时，bridge 会用
`uv run --project executas/focus-session-python <minted-tool-id>` 懒启动 executa
进程。**如果该子进程立刻退出**，harness 会在 RPC 日志里报
`tool_failed: executa process exited` —— 此时去
`cd executas/focus-session-python && uv sync` 跑一遍，可以看到真正的依赖
解析错误。

### 3. 校验 manifest

```bash
pnpm validate               # → anna-app validate --strict
```

这会本地复现服务器在提交版本时执行的同一套三层校验
（`AppManifest` Pydantic 模型 + `validate_ui_section_static` + bundle
文件检查）。

### 4. 跑 Plugin 契约测试

`tests/plugin/` 下的 pytest 用例使用已发布的
[`anna-executa-test`](https://pypi.org/project/anna-executa-test/)
助手 spawn 插件并断言 JSON-RPC 契约：

```bash
cd executas/focus-session-python
uv sync --extra dev         # 装 pytest + anna-executa-test
uv run pytest ../../tests/plugin -q
```

`pnpm test` 跑 vitest 套件，覆盖 bundle / fixture 解析。

### 5. 回放录制好的 fixture

```bash
pnpm fixture:verify         # 把 fixtures/*.jsonl 喂给 harness
pnpm fixture:summarize      # 输出 happy-path 的可读时序
```

### 更底层的调试手段

绕过 harness，直接和插件的 stdio JSON-RPC 对话：

```bash
cd executas/focus-session-python
uv run python focus_session_plugin.py <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"describe"}
{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"tool":"session","arguments":{"action":"start","duration_minutes":1,"topic":"smoke test"}}}
{"jsonrpc":"2.0","id":3,"method":"invoke","params":{"tool":"session","arguments":{"action":"get_state"}}}
EOF
```

在不连 host RPC 的情况下预览 bundle（会出 toast 提示 SDK 不可用，
但布局仍能正常渲染）：

```bash
cd bundle && python -m http.server 8080   # http://localhost:8080
```

---

## License

MIT — 见 [`LICENSE`](../../LICENSE)。

[schema]: https://github.com/openclaw/matrix-nexus/blob/main/src/schemas/anna_app.py
