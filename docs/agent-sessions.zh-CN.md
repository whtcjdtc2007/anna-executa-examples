English version: [agent-sessions.md](agent-sessions.md)

# Executa Agent Sessions（插件/anna-app 平权）

> 让 stdio Executa 插件通过反向 JSON-RPC 驱动**多轮、可调用工具的 Anna Agent
> Session** —— 与 iframe 内 `anna-app` 拥有完全相同的能力面、鉴权边界、
> 工具授权模型与 wire frame。

本文构建在 [sampling.md](sampling.zh-CN.md) 之上。Sampling 提供**单轮**
completion；Agent Session 提供**有状态、可调工具**的 agent run。

## 为什么单独一个面？

Sampling（`sampling/createMessage`）是 1-请求-1-响应。真正的 agent 工作需要：

* **持久 thread**，跨多轮用户输入。
* **工具调用**，由 host 实际执行（搜索、storage、其他插件……）。
* **流式 frame**（`delta` / `tool_call` / `tool_result` / `final`）。
* **运行中取消**。

Agent 面对齐 anna-app iframe 既有能力。目标：Python 插件和 TypeScript anna-app
对同一个 agent 工作负载**可以互换实现**。

## 前置条件

插件 manifest 必须声明两项授权：

```json
{
  "host_capabilities": ["llm.sample", "llm.agent.auto"]
}
```

| 授权              | 解锁                                  |
| ----------------- | ------------------------------------- |
| `llm.sample`      | `sampling/*`、`agent/complete`（L1） |
| `llm.agent.auto`  | `agent/session.*`（L2 多轮）         |

未声明 `llm.agent.auto` 时，`agent/session.create` 会被拒绝并返回
`AGENT_NOT_GRANTED (-32041)`。

## 鉴权链路

插件**永远不持有 bearer token**。Token 始终留在 matrix host：

```
plugin                     matrix host                       nexus
  │                             │                              │
  │── agent/session.create ────►│                              │
  │  (host 自动注入              │                              │
  │   ctx.sampling_token)        │                              │
  │                             │── POST /copilot/app/         │
  │                             │     sessions/from_sampling   │
  │                             │     Bearer = sampling_token  │
  │                             │◄── {app_session_uuid, token, │
  │                             │     thread_id, ...}          │
  │                             │   (host 缓存 token)          │
  │◄── {app_session_uuid, ...} ─│   (token 已剥离)             │
```

后续 `agent/session.run|cancel|delete` 只携带 `app_session_uuid`，
host 在出站 HTTP 上自行附加缓存的 `app_session_token`。
缓存键是 `(user_id, hash(plugin_name))`，因此插件之间无法互相劫持 session。

## SDK（Python）

```python
from executa_sdk import (
    SamplingClient, AgentSessionClient, AgentError,
    METHOD_AGENT_SESSION_CREATE,
)

agent = AgentSessionClient()
agent.attach_writer(_write_frame)  # 与 SamplingClient 共用 stdout writer

# 多轮
session = await agent.create(kind="agent", agent_submode="auto")
async for frame in session.run("帮我规划这周。"):
    if frame["event"] == "delta":
        sys.stderr.write(frame["text"])
    elif frame["event"] == "tool_call":
        ...
await session.delete()

# 单轮（L1，无 session 状态）
text = await agent.complete(prompt="总结：……", max_tokens=200)
```

`AgentError` 继承 `SamplingError`，所以一个 `except SamplingError`
能同时兜住两个面。

stdin 分发循环里按顺序尝试两个 client：

```python
if not agent.dispatch_response(msg):
    sampling.dispatch_response(msg)
```

## 与 anna-app 对称

```ts
// anna-app iframe 内
const session = await anna.agent.session({ submode: "auto" });
for await (const frame of session.run("帮我规划这周。")) {
  if (frame.event === "delta") process.stdout.write(frame.text);
}
await session.delete();
```

生命周期、frame 形状、授权门控完全一致。唯一差异是传输层：anna-app 走
postMessage，插件走 stdio JSON-RPC。

## 反向 RPC 方法

| Method                  | 用途                                       |
| ----------------------- | ----------------------------------------- |
| `agent/session.create`  | 创建 app session，返回 uuid + thread_id   |
| `agent/session.run`     | 发送用户消息，返回 frame 数组             |
| `agent/session.cancel`  | 中断指定 `run_id`                         |
| `agent/session.history` | （延后）拉历史                            |
| `agent/session.delete`  | 幂等清理                                  |
| `agent/complete`        | 无状态单轮 completion                     |

## 错误码

| Code     | 名称                              | 触发条件                                |
| -------- | --------------------------------- | --------------------------------------- |
| `-32041` | `AGENT_NOT_GRANTED`               | manifest 缺 `llm.agent.auto`           |
| `-32042` | `AGENT_INVALID_SUBMODE`           | `kind=agent` 但 submode 非法           |
| `-32043` | `AGENT_FIXED_REQUIRES_CLIENT_ID`  | `kind=fixed` 缺 `client_id`            |
| `-32044` | `AGENT_UNKNOWN_SESSION`           | uuid 不在 cache                        |
| `-32045` | `AGENT_INVALID_UUID`              | uuid 不属于本 (plugin, user)           |
| `-32046` | `AGENT_NEXUS_ERROR`               | 上游 nexus 失败                        |
| `-32047` | `AGENT_RUN_TOO_LARGE`             | run 超过 4096 帧上限                   |
| `-32048` | `AGENT_TOOL_NOT_GRANTED`          | 请求工具不在 `granted_tools`           |

## v2 限制

* `agent/session.run` 是**缓冲式**的 —— host 在 run 整体结束（`done=true`）后
  一次性返回所有 frame。SDK API（`async for frame`）已与未来真流式路径一致，
  切换时业务代码无需改动。
* `agent/session.history` 暂返回 `[]`，等待公开 GET 端点。
* 单 run 帧数硬上限 **4096**，超出返回 `AGENT_RUN_TOO_LARGE`。

## 参考

* 可运行示例：[`examples/python/executa-agent-demo`](../examples/python/executa-agent-demo/)
* SDK 源码：[`sdk/python/executa_sdk/agent.py`](../sdk/python/executa_sdk/agent.py)
* Host 实现：`matrix/src/executa/agent.py`
* Nexus mint 端点：`matrix-nexus/src/api/copilot_app.py::app_create_session_from_sampling`
* 设计文档：`matrix-nexus/docs/design/app-llm-and-agent-access.md` §17
* 开发者指南：`matrix-nexus/docs/developers/apps/llm-and-agent.md` §8
