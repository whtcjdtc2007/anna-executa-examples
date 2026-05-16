# executa-agent-demo

A reference Python Executa (tool) demonstrating **executa/app parity** for the
Anna Agent surface. This executa uses `executa_sdk.AgentSessionClient` to mint
an Anna App Session entirely through reverse JSON-RPC, then runs the agent and
streams frames — all without the executa ever holding a bearer token.

> See also: [executa-agent-demo (中文)](README.zh-CN.md)

## What's new vs. `sampling-summarizer`

`sampling-summarizer` exposes only L1 (single-shot completion). This example
adds the L2 surface — multi-turn agent runs with tool use — by going through
`agent/session.create → agent/session.run → agent/session.delete`.

| Capability | `sampling-summarizer` | `executa-agent-demo` |
|---|---|---|
| L1 completion | ✅ via `sampling/createMessage` | ✅ via `agent/complete` |
| L2 agent (multi-turn, tools) | ❌ | ✅ via `agent/session.*` |
| Executa holds bearer | ❌ | ❌ |
| Same wire format as anna-app iframe | ✅ | ✅ |

## Tools

| Name | Purpose |
|---|---|
| `ask_agent({question, label?})` | Mint a session, run one agent turn, return the final text |
| `ask_complete({prompt, max_tokens?})` | Single-shot stateless completion |

## Manifest

The manifest declares both capabilities so the user is shown exactly what they
authorize:

```jsonc
"host_capabilities": ["llm.sample", "llm.agent.auto"]
```

Without the `llm.agent.auto` capability, the Anna Agent's
`ExecutaAgentHandler` rejects `agent/session.*` calls with `AGENT_NOT_GRANTED`.

## How the auth flow works

```
executa                    Anna Agent                    Anna Server
  │                              │                              │
  │── agent/session.create ─────►│                              │
  │   (over stdio JSON-RPC)      │                              │
  │                              │── POST /sessions/from_sampling
  │                              │     Bearer = sampling_token  │
  │                              │◄── {app_session_uuid, token} │
  │                              │     (Anna Agent caches token)│
  │◄── {app_session_uuid, ...} ──│  (token is stripped)         │
  │                              │                              │
  │── agent/session.run ────────►│                              │
  │                              │── POST /copilot/app/agent ──►│
  │                              │     Bearer = app_session_token
  │                              │◄── SSE stream                │
  │◄── {frames: [...], final} ───│  (frames buffered)           │
```

Executa code is symmetric with the anna-app iframe SDK:

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

## Run locally

This example is a standard Executa (tool); its layout, packaging, and
distribution flow are identical to [`basic-tool/`](../basic-tool/) and
[`credential-tool/`](../credential-tool/). See
[`examples/python/README.md`](../README.md) ("Running", "Building as a
standalone binary", "Distributing to Anna") for the canonical recipes.
This section only covers the agent-specific deltas.

### 1. Spawn the process directly (local-method smoke test only)

```bash
cd executa-agent-demo
python executa_agent_demo.py
```

Probe `describe` over stdio:

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' \
  | python executa_agent_demo.py 2>/dev/null
```

> ⚠️ `agent/session.*` and `agent/complete` are **reverse RPC** — they
> only make sense with an Anna Agent on the other end of stdio.
> Standalone, `invoke ask_agent` will hang waiting for the reverse
> response. To exercise the full path, register against an Anna Server
> per §2.

### 2. Register against Anna Server for end-to-end runs (recommended)

Same flow as `basic-tool` / `credential-tool`: build/install the
artifact, then enroll once in Anna Admin.

```bash
# Option A: uv local distribution (fastest)
cd executa-agent-demo
uv tool install .            # exposes the `executa-agent-demo` console-script

# Option B: PyInstaller single-file binary
cd executa-agent-demo
../build_binary.sh executa-agent-demo --test
# Artifact: dist/executa-agent-demo
```

In Anna Admin, create / edit the Executa with:

* **Protocol**: `stdio`
* **Distribution**: `uv` (package = `executa-agent-demo`) /
  `Local` (Archive Path → the tar.gz from above) /
  `Binary` (HTTP URL) — pick one
* **Manifest**: keep `host_capabilities: ["llm.sample", "llm.agent.auto"]`
* **User grants**: open the Executa's **Permissions** modal on Anna Admin
  (`/executa`) and enable both **LLM Sampling** and **Agent Session — auto
  submode**. Both `ask_agent` and `ask_complete` route through
  `agent/session.create` (kind=agent, submode=auto), so the auto toggle is
  the one that matters; the optional **LLM Quota** fields cap tokens/calls
  for this Executa.

Once enrolled, any anna-app that installs the tool can call it from its
bundle:

```js
await anna.tools.invoke({
  tool_id: "<minted-tool-id>",
  method: "invoke",
  args: { name: "ask_agent", arguments: { question: "hello" } },
});
```

This exercises the full path: **client bundle → Anna Server → Anna
Agent → this executa**. Anna Agent injects `sampling_token` into the
reverse-RPC `ctx`; the executa uses `AgentSessionClient` to call back
into Anna Agent and complete the agent run.

### 3. JSON-RPC wire reference

Use the same `echo … | python … 2>/dev/null` template documented in
[`examples/python/README.md`](../README.md#protocol-interaction-examples)
for `basic-tool` / `credential-tool`. The `invoke` schema for this
executa:

```json
{"jsonrpc":"2.0","method":"invoke",
 "params":{"tool":"ask_complete",
          "arguments":{"prompt":"hello","max_tokens":64}},
 "id":2}
```

> Note: both `ask_agent` and `ask_complete` issue reverse RPC
> internally; standalone stdio probing will only show the request
> hanging. End-to-end validation must go through the §2 enrollment.

## Wire format / SDK reference

* SDK: `anna-executa-examples/sdk/python/executa_sdk/agent.py`
* Anna Agent implementation: `matrix/src/executa/agent.py`
* Anna Server mint endpoint: `matrix-nexus/src/api/copilot_app.py::app_create_session_from_sampling`
* Design spec: `matrix-nexus/docs/design/app-llm-and-agent-access.md` §17
* Plugin developer guide: `matrix-nexus/docs/developers/apps/llm-and-agent.md` §8

## Limitations (v2)

* `agent/session.run` is **buffered** — Anna Agent waits until the run completes
  and returns all frames in a list. The SDK API (`async for frame`) is identical
  to true streaming, so a future protocol bump (PROTOCOL_VERSION_V3) won't
  require code changes.
* `agent/session.history` returns an empty list pending a public GET endpoint.
* Single run frame cap: 4096.
