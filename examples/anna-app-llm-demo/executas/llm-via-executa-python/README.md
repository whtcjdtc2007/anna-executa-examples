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

This Executa exposes **two** tools:

### `complete`

| name     | parameters                                                   |
|----------|--------------------------------------------------------------|
| complete | `prompt` (required string), `system_prompt`, `max_tokens=256`|

Issues a reverse `sampling/createMessage` and returns
`{ "text": "...", "model": "...", "usage": {...}, "stopReason": "..." }`.

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
