English version: [sampling.md](sampling.md)

# Executa LLM Sampling

> 让长期运行的 Executa 插件请求 host（Anna）**代表用户** 执行一次 LLM
> 推理 —— 插件无需自带 API key、模型选择逻辑或配额追踪。

## 为什么需要

很多有用的工具想在自身工作流中调用 LLM（摘要、分类、抽取、规划……）。
没有 sampling，每个插件都得：

- 自带 API key（安全/合规噩梦），
- 自己挑模型（还得跟进模型下线），
- 自己计量计费（不可能 —— 它看不到用户的套餐）。

Executa **2.0** 用一个仿照 [MCP `sampling/createMessage`][mcp-sampling]
的反向 JSON-RPC 调用解决这个问题。插件用协议中立的方式描述想要的补全；
host（Nexus）通过用户偏好的 provider 路由、扣用户配额、回传结果。
插件只看到“黑盒文本进、黑盒文本出”。

[mcp-sampling]: https://modelcontextprotocol.io/

## 前置条件

端到端 sampling 同时需要 **三个** 条件：

1. **协商到 v2。** Host 发送 `initialize` 且 `protocolVersion: "2.0"`；
   插件以同一版本回复，并声明 `client_capabilities.sampling = {}`。
   v1 插件继续工作但不能 sampling。
2. **Manifest 声明。** 插件 `describe` 返回的 manifest 必须包含
   `host_capabilities: ["llm.sample"]`，否则 Nexus 直接以
   `-32008 not_negotiated` 拒绝。
3. **用户授权。** 用户必须在 Anna Admin 为该 Executa 打开 sampling
   开关（写入 `UserExecuta.custom_config.sampling_grant.enabled = true`，
   并配置 `maxCalls` / `maxTokensTotal` 上限）。否则 Nexus 以
   `-32001 not_granted` 拒绝。

## 线协议

`initialize` 成功后，**invoke** 请求会多两个参数：

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "invoke",
  "params": {
    "tool": "summarize",
    "arguments": { "text": "…" },
    "invoke_id": "8f1c…",
    "sampling_token": "eyJ…"
  }
}
```

插件处理 invoke 期间，可以发起一次或多次 **反向 RPC**：

```json
{
  "jsonrpc": "2.0",
  "id": "<plugin 自定义 uuid>",
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      { "role": "user", "content": { "type": "text", "text": "请总结：\n…" } }
    ],
    "maxTokens": 400,
    "systemPrompt": "你是一个简洁的助手。",
    "temperature": 0.3,
    "stopSequences": ["\n\n###"],
    "modelPreferences": {
      "hints": [{ "name": "claude-sonnet" }],
      "costPriority": 0.4,
      "speedPriority": 0.4,
      "intelligencePriority": 0.2
    },
    "includeContext": "none",
    "metadata": { "executa_invoke_id": "8f1c…" }
  }
}
```

Agent 转给 Nexus，Nexus 会：

1. 校验 `sampling_token`（audience `executa-sampling`，
   必须与 `executa_tool_id` + `tool_invoke_id` 匹配）。
2. 加载用户授权 + manifest 声明。
3. 维护单次 invoke 的 Redis 计数器（TTL 1 800 s）。
4. 选模型：
   - `modelPreferences.hints[*].name` 命中活跃模型 → 用之
     （`costPriority > 0` 时 ties 取最便宜的），
   - **否则回退到用户保存的 `preferred_model`**，
   - 再否则回退到默认 provider 的最便宜活跃模型。
5. 通过对应 provider 完成补全。
6. 以 `LLMRequestType.EXECUTA_SAMPLING`（计费类目
   `agent_subroutine`）记录 token 使用。
7. 把结果回给 host，host 转给插件：

```json
{
  "jsonrpc": "2.0",
  "id": "<plugin 自定义 uuid>",
  "result": {
    "role": "assistant",
    "content": { "type": "text", "text": "…" },
    "model": "claude-3-5-sonnet-20241022",
    "stopReason": "endTurn",
    "usage": { "inputTokens": 312, "outputTokens": 187, "totalTokens": 499 },
    "_meta": { "provider": "anthropic", "latencyMs": 1432, "quotaConsumed": 0.0021 }
  }
}
```

失败时返回 `error`，对照表见
[protocol-spec.zh-CN.md](protocol-spec.zh-CN.md#sampling-%E4%B8%93%E7%94%A8%E9%94%99%E8%AF%AF%E7%A0%81v2)。

## 单次 invoke 上限（v1）

| 上限 | 默认 | 来源 |
|------|------|------|
| 单次调用 `maxTokens` | 8 192 | `DEFAULT_SAMPLING_MAX_TOKENS_PER_CALL`（host） |
| 单 invoke 调用次数 | 8 | `sampling_grant.maxCalls`，host 强制 ≤8 |
| 单 invoke 累计 tokens | 32 000 | `sampling_grant.maxTokensTotal`，host 强制 ≤32 000 |
| `sampling_token` TTL | 600 秒 | `DEFAULT_SAMPLING_TTL_SECONDS` |
| `includeContext` | v1 仅支持 `"none"` | 其它值返回 `-32004` |

超过 `maxCalls` → `-32006`；超过累计 tokens → `-32007`。两者均为终态，
插件不能重试。

## 模型选择优先级

发了 `modelPreferences` 时：

```
1. hints[*].name  →  按子串匹配活跃模型（大小写不敏感）。
                     若 costPriority > 0，并列时取最便宜。
2. (没 hints / 无匹配)  →  user.settings.preferred_model
3. (preferred_model 未设)  →  默认 provider 的最便宜活跃模型
```

设计上偏保守：插件通常应 **完全省略** `modelPreferences`，让用户的模型
偏好生效。Hints 仅用于强依赖某模型族的工具（如必须长上下文模型的
代码生成工具）。

## SDK 一览

| SDK | 文件 | 入口 |
|-----|------|------|
| Python | [`sdk/python/executa_sdk/sampling.py`](../sdk/python/executa_sdk/sampling.py) | `SamplingClient.create_message(...)` |
| Node.js | [`sdk/nodejs/sampling.js`](../sdk/nodejs/sampling.js) | `new SamplingClient().createMessage({...})` |
| Go | [`sdk/go/sampling/sampling.go`](../sdk/go/sampling/sampling.go) | `sampling.New(nil).CreateMessage(req, timeout)` |

可运行示例：
[`examples/python/sampling-summarizer/sampling_summarizer.py`](../examples/python/sampling-summarizer/sampling_summarizer.py)、
[`examples/nodejs/sampling-tool.js`](../examples/nodejs/sampling-tool.js)、
[`examples/go/sampling-tool/`](../examples/go/sampling-tool/)。

## 常见坑

- **进程模型。** 同一个 stdin 读循环既会收到 agent 主动请求，也会收到
  host 对你反向 RPC 的回复。各 SDK 的 `dispatchResponse` /
  `DispatchResponse` 通过“是否有 `method` 字段”来区分两者。
- **不要 invoke 完就 `process.exit()`。** Sampling 是异步的，可能在原
  invoke 写完后才完成；过早退出会丢掉在途的反向 RPC。
- **要把 `invoke_id` 回传到 metadata。** 这是计费审计的关联键。
- **不要打包 API key。** 如果你在插件里读 `OPENAI_API_KEY`，几乎肯定
  应该改用 sampling。
