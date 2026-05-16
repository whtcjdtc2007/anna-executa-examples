# executa-agent-demo（中文）

一个 Python Executa（tool）示例，演示 executa 与 anna-app 在 Anna Agent 维度的**完全平权**。
它使用 `executa_sdk.AgentSessionClient` 通过反向 JSON-RPC 创建 Anna App Session，
执行 agent 多轮调用、流式接收 frame —— 整个过程 executa **永远不持有 bearer token**。

## 与 `sampling-summarizer` 的差异

| 能力 | `sampling-summarizer` | `executa-agent-demo` |
|---|---|---|
| L1 单轮 completion | ✅ 通过 `sampling/createMessage` | ✅ 通过 `agent/complete` |
| L2 agent（多轮 + tool） | ❌ | ✅ 通过 `agent/session.*` |
| 插件持有 bearer | ❌ | ❌ |
| 与 anna-app iframe wire format | ✅ | ✅ |

## 工具

| 名称 | 用途 |
|---|---|
| `ask_agent({question, label?})` | 创建 session、跑一轮 agent、返回最终文本 |
| `ask_complete({prompt, max_tokens?})` | 单轮无状态 completion |

## Manifest

```jsonc
"host_capabilities": ["llm.sample", "llm.agent.auto"]
```

未声明 `llm.agent.auto` 时，Anna Agent 的 `ExecutaAgentHandler` 直接拒绝（`AGENT_NOT_GRANTED`）。

## 鉴权流程

```
executa                    Anna Agent                    Anna Server
  │                              │                              │
  │── agent/session.create ─────►│                              │
  │                              │── POST /sessions/from_sampling
  │                              │     Bearer = sampling_token  │
  │                              │◄── {app_session_uuid, token} │
  │                              │     (Anna Agent 缓存 token)   │
  │◄── {app_session_uuid, ...} ──│  (token 已剥离)              │
  │                              │                              │
  │── agent/session.run ────────►│                              │
  │                              │── POST /copilot/app/agent ──►│
  │                              │     Bearer = app_session_token
  │                              │◄── SSE 流                    │
  │◄── {frames: [...], final} ───│  (帧已缓冲)                  │
```

Executa 代码与 anna-app iframe SDK 对称：

```python
session = await agent.create(kind="agent", agent_submode="auto")
async for frame in session.run("hello"):
    if frame["event"] == "delta":
        ...
await session.delete()
```

```ts
const session = await anna.agent.session({ submode: "auto" });
for await (const frame of session.run("hello")) {
  if (frame.event === "delta") { /* ... */ }
}
await session.delete();
```

## 本地运行

本示例是一个标准 Executa（tool），与 [`basic-tool/`](../basic-tool/)、
[`credential-tool/`](../credential-tool/) 的形态、打包、分发流程完全一致 ——
参见 [`examples/python/README.zh-CN.md`](../README.zh-CN.md) 的「运行方式 /
构建为独立二进制 / 分发到 Anna」章节。本节只列出与 agent 反向 RPC 相关的差异点。

### 1. 直接起进程（仅能验证本地方法）

```bash
cd executa-agent-demo
python executa_agent_demo.py
```

用 stdio 管道试探 `describe`：

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' \
  | python executa_agent_demo.py 2>/dev/null
```

> ⚠️ `agent/session.*`、`agent/complete` 都是**反向 RPC** —— 必须有 Anna
> Agent 在 stdio 另一端应答才有意义。脱离 Anna Agent 单跑，`invoke ask_agent`
> 会卡在反向请求上。要打通完整链路，按下面 §2 注册到 Anna Server。

### 2. 注册到 Anna Server 跑端到端测试（推荐）

与 `basic-tool` / `credential-tool` 一样，先安装/构建产物，再在 Anna Admin
按对应分发方式登记一次：

```bash
# 方案 A：uv 本地分发（最快）
cd executa-agent-demo
uv tool install .            # 暴露 console-script `executa-agent-demo`

# 方案 B：PyInstaller 单文件
cd executa-agent-demo
../build_binary.sh executa-agent-demo --test
# 产物：dist/executa-agent-demo
```

在 Anna Admin 创建 / 编辑 Executa：

* **协议**：`stdio`
* **分发方式**：`uv`（包名 = `executa-agent-demo`）/ `Local`（Archive 路径
  指向上面打包出的 tar.gz）/ `Binary`（HTTP 下载地址）—— 任选其一
* **Manifest**：必须保留 `host_capabilities: ["llm.sample", "llm.agent.auto"]`
* **用户授权**：在 Anna Admin（`/executa`）打开该 Executa 的 **权限** 弹窗，
  开启 **LLM 采样** 与 **Agent 会话 — auto 子模式**。`ask_agent` 与
  `ask_complete` 都会走 `agent/session.create`（kind=agent，submode=auto），
  所以 auto 开关是必须项；可选的 **LLM 额度** 字段用于限制该 Executa 的
  token / 每日调用上限。

登记完成后，安装了该 tool 的 anna-app 即可在自己的 bundle 里通过：

```js
await anna.tools.invoke({
  tool_id: "<minted-tool-id>",
  method: "invoke",
  args: { name: "ask_agent", arguments: { question: "hello" } },
});
```

触发完整链路：**客户端 bundle → Anna Server → Anna Agent → 本 executa**，
Anna Agent 自动把 `sampling_token` 注入反向 RPC 的 `ctx`，executa 用
`AgentSessionClient` 反向打回 Anna Agent 完成 agent 调用。

### 3. JSON-RPC 协议交互参考

参考 [`examples/python/README.zh-CN.md`](../README.zh-CN.md#协议交互示例)
中 `basic-tool` / `credential-tool` 的同款 `echo … | python … 2>/dev/null`
模板。本 executa 的 `invoke` 请求 schema：

```json
{"jsonrpc":"2.0","method":"invoke",
 "params":{"tool":"ask_complete",
          "arguments":{"prompt":"hello","max_tokens":64}},
 "id":2}
```

> 注意：`ask_agent` / `ask_complete` 内部都会发起反向 RPC，stdio 单测只能
> 看到请求挂起；端到端验证仍需走 §2 的注册路径。

## Wire format / SDK 参考

* SDK: `anna-executa-examples/sdk/python/executa_sdk/agent.py`
* Anna Agent 实现: `matrix/src/executa/agent.py`
* Anna Server mint 端点: `matrix-nexus/src/api/copilot_app.py::app_create_session_from_sampling`
* 设计文档: `matrix-nexus/docs/design/app-llm-and-agent-access.md` §17
* 开发者文档: `matrix-nexus/docs/developers/apps/llm-and-agent.md` §8

## v2 限制

* `agent/session.run` 是**缓冲式**的 —— Anna Agent 在 run 完整结束后一次性返回所有 frame；SDK API 与真实流式一致，未来切换不需要改业务代码。
* `agent/session.history` 暂返回空数组，等待 public GET 端点。
* 单 run 帧数上限 4096。
