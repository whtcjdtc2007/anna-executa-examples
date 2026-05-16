中文版本请参阅 [sampling.zh-CN.md](sampling.zh-CN.md)

# Executa LLM Sampling

> Lets a long-running Executa plugin ask its host (Anna) to perform an
> LLM completion **on the user's behalf** — without the plugin needing
> its own LLM API key, model selection logic, or quota tracking.

> **For multi-turn / tool-using agent runs**, see the
> [agent-sessions.md](agent-sessions.md) guide and the
> [`executa-agent-demo`](../examples/python/executa-agent-demo/) example —
> they extend this same reverse-RPC pattern to bring **plugin/app parity**
> with the anna-app iframe agent surface.

## Why?

Many useful tools want to invoke an LLM as part of their work
(summarize, classify, extract, plan…). Without sampling each plugin
would have to:

- ship its own API key (security + compliance nightmare),
- pick a model (and stay current with model deprecations),
- meter and bill itself (impossible — it doesn't see the user's plan).

Executa **2.0** solves this with a reverse JSON-RPC call modelled on
[MCP `sampling/createMessage`][mcp-sampling]. The plugin describes the
desired completion in protocol-neutral terms; the host (Nexus) routes it
through the user's preferred provider, charges the user's quota, and
returns the result. The plugin sees only opaque text in / opaque text
out.

[mcp-sampling]: https://modelcontextprotocol.io/

## Pre-requisites

End-to-end sampling requires **all three** of:

1. **v2 protocol negotiation.** The host sends `initialize` with
   `protocolVersion: "2.0"`; the plugin must respond with the same
   version and advertise `client_capabilities.sampling = {}`. v1
   plugins continue to work but cannot sample.
2. **Manifest declaration.** The plugin's `describe` manifest must
   include `host_capabilities: ["llm.sample"]`. Without this Nexus
   refuses with `-32008 not_negotiated`.
3. **User grant.** The end user must enable sampling for this Executa
   in their Anna Admin panel (this writes
   `UserExecuta.custom_config.sampling_grant.enabled = true`,
   along with `maxCalls` and `maxTokensTotal` per-invoke caps). Without
   this grant Nexus refuses with `-32001 not_granted`.

## Wire protocol

After `initialize` succeeds, the **invoke** request carries two new
params:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "invoke",
  "params": {
    "tool": "summarize",
    "arguments": { "text": "…" },
    "invoke_id": "8f1c…",        // per-invoke correlation ID
    "sampling_token": "eyJ…"     // short-lived JWT, only present when grant is on
  }
}
```

While the plugin is processing this invoke it MAY emit one or more
**reverse RPC** requests:

```json
{
  "jsonrpc": "2.0",
  "id": "<plugin-chosen uuid>",
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      { "role": "user", "content": { "type": "text", "text": "Summarize:\n…" } }
    ],
    "maxTokens": 400,
    "systemPrompt": "You are a concise assistant.",
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

The Agent forwards the request to Nexus, which:

1. Validates the bound `sampling_token` (audience `executa-sampling`,
   matches `executa_tool_id` + `tool_invoke_id`).
2. Loads the user's grant + manifest declaration.
3. Increments per-invoke counters (Redis hash, TTL 1 800 s).
4. Picks a model:
   - if `modelPreferences.hints[*].name` matches an active model → use
     it (cheapest match wins when `costPriority > 0`),
   - **else falls back to the user's saved `preferred_model`,**
   - else falls back to the default provider's cheapest active model.
5. Performs the completion via the chosen provider.
6. Records token usage under `LLMRequestType.EXECUTA_SAMPLING`
   (billing category: `agent_subroutine`).
7. Returns the result to the host, which forwards it to the plugin:

```json
{
  "jsonrpc": "2.0",
  "id": "<plugin-chosen uuid>",
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

When sampling fails the response carries an `error` block — see
[protocol-spec.md](protocol-spec.md#sampling-specific-error-codes-v2)
for the code table.

## Per-invoke caps (v1 limits)

| Cap | Default | Where |
|-----|---------|-------|
| `maxTokens` per call | 8 192 | `DEFAULT_SAMPLING_MAX_TOKENS_PER_CALL` (host) |
| Calls per `invoke_id` | 8 | `sampling_grant.maxCalls`, capped at 8 by host |
| Total tokens per `invoke_id` | 32 000 | `sampling_grant.maxTokensTotal`, capped at 32 000 |
| `sampling_token` TTL | 600 s | `DEFAULT_SAMPLING_TTL_SECONDS` |
| `includeContext` | only `"none"` is supported in v1 | host rejects others as `-32004` |

Exceeding `maxCalls` returns `-32006`; exceeding cumulative tokens
returns `-32007`. Both are terminal — the plugin cannot retry.

## Model selection precedence

When the plugin sends `modelPreferences`:

```
1. hints[*].name  →  first active model whose model_name contains the hint
                     (case-insensitive substring match).
                     If costPriority > 0, ties break to cheapest.
2. (no hints, or none matched)  →  user.settings.preferred_model
3. (preferred_model unset)      →  default provider's cheapest active model
```

This is intentionally conservative: plugins should normally **omit**
`modelPreferences` entirely so that user-level model preferences apply.
Hints exist for tools whose quality strictly depends on a particular
model family (e.g. code-gen tools that require a long-context model).

## SDK summary

| SDK | File | Entry point |
|-----|------|-------------|
| Python | [`sdk/python/executa_sdk/sampling.py`](../sdk/python/executa_sdk/sampling.py) | `SamplingClient.create_message(...)` |
| Node.js | [`sdk/nodejs/sampling.js`](../sdk/nodejs/sampling.js) | `new SamplingClient().createMessage({...})` |
| Go | [`sdk/go/sampling/sampling.go`](../sdk/go/sampling/sampling.go) | `sampling.New(nil).CreateMessage(req, timeout)` |

Runnable plugin examples live under
[`examples/python/sampling-summarizer/sampling_summarizer.py`](../examples/python/sampling-summarizer/sampling_summarizer.py),
[`examples/nodejs/sampling-tool.js`](../examples/nodejs/sampling-tool.js),
and [`examples/go/sampling-tool/`](../examples/go/sampling-tool/).

## Common pitfalls

- **Plugin process design.** The same stdin reader receives both
  agent-initiated requests AND host responses to your reverse RPCs. Each
  SDK's `dispatchResponse` / `DispatchResponse` helper distinguishes the
  two by the absence of a `method` field.
- **Don't `process.exit()` after a single invoke.** Sampling is async
  and may complete after the original `invoke` is finished writing —
  exiting early drops in-flight reverse RPCs.
- **Echo `invoke_id`.** Always include `invoke_id` in `metadata` so
  Nexus can attribute spend correctly.
- **Don't ship API keys.** If you find yourself reading
  `OPENAI_API_KEY` from env in a plugin, you almost certainly want
  sampling instead.
