# Visual Brand — Anna App sample

A focused canvas that demonstrates the **app-side** half of the
`executa-llm-image-generation` RFC v2:

| UI action       | HTTP endpoint                                  |
| --------------- | ---------------------------------------------- |
| **Generate**    | `POST /api/v1/copilot/app/image/generate`      |
| **Restyle**     | `POST /api/v1/copilot/app/image/edit`          |
| **Persist**     | `POST /api/v1/copilot/app/upload` (inline)     |

Auth is the `app_session_token` minted by the AnnaApp runtime during
handshake — the app holds zero long-lived credentials and never touches
an LLM API key.

## Install + run

```bash
pnpm install
pnpm dev                       # anna-app dev — opens the iframe + harness
```

In standalone preview (`anna-app dev` without a live Nexus account) the
fetch calls go to `window.location.origin`, which the harness mocks.
For end-to-end testing point at a real Nexus:

```bash
anna-app login --host https://nexus.example.com
pnpm dev
```

## Permissions / grants

The end-user must have toggled the matching grant in their Anna Admin
panel for this app:

- `image_grant.generate = true`           → enables **Generate**
- `image_grant.edit = true`               → enables **Restyle**
- `upload_grant.enabled = true`           → enables **Persist**
- `upload_grant.allowed_mime_types ⊃ image/png` (default)

Without the grant, the host returns HTTP 403 with body
`{"code":-32101,"message":"image_not_granted"}` (or `-32201`); the app
surfaces this verbatim in the status line.

## See also

- Host RFC: [matrix-nexus/docs/design/executa-llm-image-generation.md](../../../matrix-nexus/docs/design/executa-llm-image-generation.md)
- Plugin-side sample (reverse-RPC): [anna-executa-examples/examples/python/image-poster/](../../../anna-executa-examples/examples/python/image-poster/)
- Fixtures: [`fixtures/happy-path.jsonl`](fixtures/happy-path.jsonl)
