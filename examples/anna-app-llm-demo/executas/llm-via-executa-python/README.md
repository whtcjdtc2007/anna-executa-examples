# llm-via-executa (python)

A tiny Executa that performs an LLM completion **on behalf of the
calling Anna App**, by issuing a reverse JSON-RPC
`sampling/createMessage` to the host.

This is the second LLM path the [`anna-app-llm-demo`](../../README.md)
app exposes in its UI — pick it from the "LLM source" selector to send
the prompt through this Executa instead of calling `anna.llm.complete`
directly.

## How the host wires it up

```
iframe ── anna.tools.invoke({tool_id, method:"complete", args:{prompt}}) ──▶
          ┌──────────────────────────────┐
          │   Anna host (matrix-nexus)    │
          └────────────────┬─────────────┘
                           │  stdio JSON-RPC
                           ▼
          ┌──────────────────────────────┐
          │   this Executa (Python)       │
          │   sampling/createMessage  ────┼─▶ host LLM ──▶ assistant text
          └──────────────────────────────┘
```

## Tools

This Executa exposes **three** tools:

### `complete`

Runs a single-turn completion and showcases the **full
`sampling/createMessage` attribute surface**.

| parameter               | type    | notes                                             |
|-------------------------|---------|---------------------------------------------------|
| `prompt` (required)     | string  | user prompt                                       |
| `system_prompt`         | string  | optional system instruction                       |
| `max_tokens`            | integer | 16–8192 (the sampling per-call cap), default 256  |
| `temperature`           | number  | 0.0–2.0; omit → host uses 0.7 (not provider default) |
| `stop`                  | string[]| optional stop sequences                           |
| `model_hint`            | string  | preferred model name (`modelPreferences.hints`)   |
| `cost_priority`         | number  | `modelPreferences.costPriority` 0.0–1.0           |
| `speed_priority`        | number  | `modelPreferences.speedPriority` 0.0–1.0          |
| `intelligence_priority` | number  | `modelPreferences.intelligencePriority` 0.0–1.0   |

Issues a reverse `sampling/createMessage` and returns
`{ "text", "model", "usage", "stopReason", "modelPreferences" }`.

### `sample_chain`

Runs **N sequential `sampling/createMessage` calls inside a single tool
invoke**, each step feeding the previous answer back as context. This is
the tool that exercises the parts of sampling that a single `complete`
cannot:

* the per-invoke **`max_calls` / `max_tokens_total`** quota (the host
  counts every call under one `invoke_id`); and
* the host's **automatic sampling-token renewal**. A long chain outlives
  the short-lived sampling token, so without renewal the later steps used
  to fail with `[-32001] invalid sampling token: Signature has expired.`
  The host now detects the near/just-expired token and transparently
  re-mints it via `POST /api/v1/copilot/sampling/renew` between calls, so
  every step succeeds on a fresh token.

| parameter                              | type    | notes                                        |
|----------------------------------------|---------|----------------------------------------------|
| `prompt` (required)                    | string  | seed prompt for step 1                       |
| `steps`                                | integer | 1–8, default 3                               |
| `system_prompt`                        | string  | applied to every step                        |
| `max_tokens`                           | integer | per-step, 16–8192, default 128               |
| `temperature`                          | number  | applied to every step                        |
| `delay_s`                              | number  | sleep between steps; set large to stretch the invoke past the token TTL and prove renewal |
| `model_hint` / `*_priority`            | —       | same `modelPreferences` fields as `complete` |

Returns `{ "text", "steps":[...], "stepCount", "modelsUsed",
"totalUsage", "modelPreferences" }`.

> **Reproducing the original instability:** call `sample_chain` with
> `steps=6` and `delay_s` set so that `steps * delay_s` exceeds the
> sampling-token TTL (default 10 min). Before the fix the run died partway
> with `Signature has expired`; now it completes, and the host log shows
> `🔁 sampling token renewed invoke_id=…` between steps.

### `agent_session`

Drives the host's **agent session** surface over reverse-RPC
(`agent/session.*`), the same operations the app can reach directly via
`anna.agent.session.*`. Pick the **Reverse RPC** transport in the demo UI
to route through this tool.

| parameter           | notes                                                        |
|---------------------|--------------------------------------------------------------|
| `op` (required)     | `create` \| `run` \| `cancel` \| `history` \| `refresh` \| `delete` \| `list` |
| `app_session_uuid`  | required for `run` / `cancel` / `history` / `refresh` / `delete` |
| `prompt`            | used by `run`                                                |
| `submode`           | used by `create` (`auto` \| `fixed`, default `auto`)         |
| `system_prompt`     | optional session-level system prompt set at `create`; applies to every run unless a per-run `systemPrompt` overrides it (the platform safety floor always wins) |
| `ttl_seconds`       | optional; used by `refresh` (default host-side)              |

`refresh` issues an `agent/session.refresh` reverse-RPC: the host
re-mints the short-lived, per-executa **sampling-scoped** capability
token and slides the session's idle deadline, returning the fresh
lifecycle (`expires_at`, `max_lifetime_at`, `idle_ttl_seconds`). The
plugin re-caches the returned token so subsequent ops keep working
without a re-`create`. Each op returns `{ "op": op, ... }`.

> The `agent_session` tool requires the manifest's
> `llm.agent.auto` grant (see `MANIFEST` in the plugin), which the host
> exchanges for the per-call sampling token.


## Local dev

From the app root (`anna-app-llm-demo/`):

```bash
pnpm dev:real   # uses your saved PAT; sampling reaches a real model
```

This executa is auto-discovered by `anna-app dev` because it lives at
`<app-dir>/executas/llm-via-executa-python/` and ships an
`executa.json`.

## Replacing the placeholder tool_id

The host keys this Executa by the **server-assigned tool_id** (resolved
from the on-disk shim name / nexus `executable_name or tool_id`), not by
any name the plugin self-declares — so `MANIFEST` no longer carries a
`name` field. `executa.json.tool_id` is set to the placeholder
`tool-test-llm-via-executa-12345678`. For real distribution mint a real
ID at <https://anna.partners/executa> and update **three** places:

1. `executa.json` → `tool_id`
2. The app's `../../manifest.json` → `required_executas[]` and
   `host_api.tools` (both must match exactly)
3. The bundle's `../../bundle/app.js` → `EXECUTA_TOOL_ID` constant
