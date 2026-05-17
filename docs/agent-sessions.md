中文版本请参阅 [agent-sessions.zh-CN.md](agent-sessions.zh-CN.md)

# Executa Agent Sessions (plugin/app parity)

> Lets a stdio Executa plugin drive **multi-turn, tool-using Anna Agent
> sessions** through reverse JSON-RPC — the same surface area an
> in-iframe `anna-app` gets, with the same auth boundaries, the same
> tool-grant model, and the same wire frames.

This builds on top of [sampling.md](sampling.md). Sampling gives you
**single-turn** completions; agent sessions give you **stateful, tool-using
agent runs**.

## Why a separate surface?

Sampling (`sampling/createMessage`) is one request → one response. Real
agentic work needs:

* **Persistent thread** across many user turns.
* **Tool calls** that the host runs (search, storage, other plugins).
* **Streaming frames** (`delta`, `tool_call`, `tool_result`, `final`).
* **Cancellation** mid-run.

The agent surface mirrors what an `anna-app` iframe can already do today.
Goal: a Python plugin and a TypeScript anna-app should be **drop-in
interchangeable** for the same agent workload.

## Pre-requisites

Plugin manifest must declare both grants:

```json
{
  "host_capabilities": ["llm.sample", "llm.agent.auto"]
}
```

| Grant            | Unlocks                              |
| ---------------- | ------------------------------------ |
| `llm.sample`     | `sampling/*`, `agent/complete` (L1)  |
| `llm.agent.auto` | `agent/session.*` (L2 multi-turn)    |

Without `llm.agent.auto`, calls to `agent/session.create` fail with
`AGENT_NOT_GRANTED (-32041)`.

## Auth chain

The plugin **never holds a bearer token**. Matrix host owns the secret:

```
plugin                     matrix host                       nexus
  │                             │                              │
  │── agent/session.create ────►│                              │
  │  (ctx.sampling_token        │                              │
  │   injected by host)         │                              │
  │                             │── POST /copilot/app/         │
  │                             │     sessions/from_sampling   │
  │                             │     Bearer = sampling_token  │
  │                             │◄── {app_session_uuid, token, │
  │                             │     thread_id, ...}          │
  │                             │   (host caches token)        │
  │◄── {app_session_uuid, ...} ─│   (token stripped)           │
```

Subsequent `agent/session.run|cancel|delete` from the plugin only carry
the `app_session_uuid`; the host attaches the cached `app_session_token`
on its outbound HTTP call. The token cache is keyed by
`(user_id, hash(plugin_name))` so plugins can never hijack each other's
sessions.

## SDK (Python)

```python
from executa_sdk import (
    SamplingClient, AgentSessionClient, AgentError,
    METHOD_AGENT_SESSION_CREATE,
)

agent = AgentSessionClient()
agent.attach_writer(_write_frame)  # same stdout writer as SamplingClient

# multi-turn
session = await agent.create(kind="agent", agent_submode="auto")
async for frame in session.run("Plan my week."):
    if frame["event"] == "delta":
        sys.stderr.write(frame["text"])
    elif frame["event"] == "tool_call":
        ...
await session.delete()

# single-turn (L1, no session state)
text = await agent.complete(prompt="Summarize: ...", max_tokens=200)
```

`AgentError` subclasses `SamplingError`, so a single `except SamplingError`
covers both surfaces.

In your stdin dispatch loop, route responses by trying both clients:

```python
if not agent.dispatch_response(msg):
    sampling.dispatch_response(msg)
```

## Symmetry with anna-app

```ts
// inside an anna-app iframe
const session = await anna.agent.session({ submode: "auto" });
for await (const frame of session.run("Plan my week.")) {
  if (frame.event === "delta") process.stdout.write(frame.text);
}
await session.delete();
```

Same lifecycle, same frame shapes, same grant gating. The only difference
is transport: anna-app uses a postMessage channel, plugin uses stdio
JSON-RPC.

## Reverse-RPC methods

| Method                  | Purpose                                  |
| ----------------------- | ---------------------------------------- |
| `agent/session.create`  | Mint app session, return uuid + thread_id |
| `agent/session.run`     | Send user message, receive frame array   |
| `agent/session.cancel`  | Abort an in-flight `run_id`              |
| `agent/session.history` | (deferred) fetch persisted history       |
| `agent/session.delete`  | Idempotent teardown                      |
| `agent/complete`        | Stateless single-turn completion         |

## Error codes

| Code     | Name                  | When                                    |
| -------- | --------------------- | --------------------------------------- |
| `-32041` | `AGENT_NOT_GRANTED`   | Manifest missing `llm.agent.auto`       |
| `-32042` | `AGENT_INVALID_SUBMODE` | `kind=agent` without valid submode    |
| `-32043` | `AGENT_FIXED_REQUIRES_CLIENT_ID` | `kind=fixed` w/o `client_id`  |
| `-32044` | `AGENT_UNKNOWN_SESSION` | uuid not in cache                     |
| `-32045` | `AGENT_INVALID_UUID`  | uuid not owned by this plugin/user      |
| `-32046` | `AGENT_NEXUS_ERROR`   | upstream nexus failure                  |
| `-32047` | `AGENT_RUN_TOO_LARGE` | run exceeded 4096-frame buffer cap      |
| `-32048` | `AGENT_TOOL_NOT_GRANTED` | requested a tool not in `granted_tools` |

## v2 limitations

* `agent/session.run` is **buffered** — host accumulates SSE frames and
  returns once `done=true`. SDK API (`async for frame`) matches the
  future real-time path; no business-code change needed when the host
  switches to streaming.
* `agent/session.history` returns `[]` until a public GET endpoint lands.
* Hard cap: **4096 frames per run**. Exceed → `AGENT_RUN_TOO_LARGE`.

## Reference

* Working example: [`examples/python/executa-agent-demo`](../examples/python/executa-agent-demo/)
* SDK source: [`sdk/python/executa_sdk/agent.py`](../sdk/python/executa_sdk/agent.py)
* Host implementation: `matrix/src/executa/agent.py`
* Nexus mint endpoint: `matrix-nexus/src/api/copilot_app.py::app_create_session_from_sampling`
* Design doc: `matrix-nexus/docs/design/app-llm-and-agent-access.md` §17
* Developer guide: `matrix-nexus/docs/developers/apps/llm-and-agent.md` §8
