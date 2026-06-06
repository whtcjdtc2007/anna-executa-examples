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

## Tool

| name     | parameters                                                   |
|----------|--------------------------------------------------------------|
| complete | `prompt` (required string), `system_prompt`, `max_tokens=256`|

Returns `{ "text": "...", "model": "...", "usage": {...}, "stopReason": "..." }`.

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
