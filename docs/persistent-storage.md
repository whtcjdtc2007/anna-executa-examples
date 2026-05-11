中文版本请参阅 [persistent-storage.zh-CN.md](persistent-storage.zh-CN.md)

# Anna Persistent Storage (APS)

> Gives an Executa plugin a small, durable, **per-user** key/value +
> object store hosted by Anna — no cloud account, no cookies, no DB of
> its own. Quota and access control are enforced by the host.

## Why?

Many real plugins need to remember things between invocations:

- **scratch state**: "where did the last run leave off?"
- **derived caches**: "the OCR result for image X is …"
- **user-owned files**: "here is the PDF the assistant generated."

Without APS each plugin would have to ship credentials to its own
backend, manage retention/encryption, and reinvent quota plumbing. APS
exposes a stable, scoped JSON-RPC surface so the plugin can stay
stateless from a deployment perspective and still keep useful state.

## Pre-requisites

End-to-end APS access requires **all three** of:

1. **v2 protocol negotiation.** The host sends `initialize` with
   `protocolVersion: "2.0"`; the plugin must respond with the same
   version and advertise `client_capabilities.storage = {}`. v1
   plugins cannot use APS.
2. **Manifest declaration.** The plugin's `describe` manifest must
   include `host_capabilities: ["storage.user"]` (or `storage.app`,
   `storage.tool` if it needs scopes other than the end-user's drive).
   Without this Nexus refuses with `-32008 not_negotiated`.
3. **User grant.** The end user must enable persistent storage for
   this Executa in their Anna Admin panel. The grant writes
   `UserExecuta.custom_config.storage_grant.scopes = ["user", …]`
   together with `quotaBytes` and `objectMaxBytes` overrides.
   Without this grant Nexus refuses with `-32001 not_granted`.

## Wire protocol

After `initialize` succeeds, the plugin issues reverse JSON-RPC calls
on the existing Executa transport. Each call carries the per-invoke
`storage_token` minted by Matrix Agent:

```json
{
  "jsonrpc": "2.0",
  "id": 12,
  "method": "storage/kv_set",
  "params": {
    "scope": "user",
    "key": "lastRun/cursor",
    "value": { "page": 7, "ts": "2026-05-01T11:22:33Z" },
    "ttl_seconds": 86400,
    "storage_token": "eyJ…"
  }
}
```

### Methods

| Method                      | Purpose                                       |
|-----------------------------|-----------------------------------------------|
| `storage/kv_get`            | Read a single JSON value by key.              |
| `storage/kv_set`            | Write a JSON value (≤ 64 KB by default).      |
| `storage/kv_delete`         | Soft-delete a key (recoverable for 7 days).   |
| `storage/kv_list`           | List keys by prefix; supports cursor paging.  |
| `storage/files_upload_init` | Get a presigned PUT URL for an object.        |
| `storage/files_finalize`    | Commit the upload after PUT succeeds.         |
| `storage/files_download_url`| Mint a time-limited GET URL.                  |
| `storage/files_list`        | List objects by path prefix.                  |
| `storage/files_delete`      | Soft-delete an object.                        |
| `storage/quota_status`      | Inspect used / remaining bytes for the user.  |

### Scopes

| Scope   | Owner               | Visibility                              |
|---------|---------------------|-----------------------------------------|
| `user`  | The end user        | Their own dashboards & every plugin they grant. |
| `app`   | The Anna App bundle | Shared across all installs of that app for one user. |
| `tool`  | The Executa plugin  | Strictly local to (user × executa).     |

`user` is the most powerful — and the most carefully gated. Plugins
should default to `tool` for transient state and ask for `user` only
when the user clearly benefits from cross-tool reuse (e.g. saving a
generated PDF into "My Files").

## Error codes

All APS errors come back as JSON-RPC errors with these stable codes:

| `errorCode`           | Meaning                                                |
|-----------------------|--------------------------------------------------------|
| `not_granted`         | User has not enabled storage for this Executa.         |
| `not_negotiated`      | v2 capability not advertised by the plugin.            |
| `not_available`       | Anna has APS globally disabled (gradual rollout).      |
| `forbidden_scope`     | Token does not authorize the requested scope.          |
| `not_found`           | Key / path missing.                                    |
| `precondition_failed` | `if_match` ETag did not match current generation.      |
| `value_too_large`     | KV value above per-call ceiling.                       |
| `quota_exceeded`      | Per-user byte budget exhausted.                        |
| `rate_limited`        | Too many writes/reads per invocation (5 / 20 default). |
| `upstream`            | Network or backend error talking to Nexus.             |

Plugins SHOULD treat `quota_exceeded` and `rate_limited` as
**non-retryable** within the same invocation, and `upstream` as
retryable with backoff.

## Best practices

1. **Prefer `tool` scope** unless the data is genuinely user-owned.
2. **Keep individual values small.** APS is JSON, not a blob store —
   anything bigger than a few KB belongs in `files_upload_init`.
3. **Always pass `if_match`** on overwrite to avoid lost updates.
4. **Set TTLs** on cache-shaped data so storage doesn't fill up.
5. **Encode metadata in the key**, not the value — `notes/{noteId}`
   is searchable via `kv_list`, embedded JSON fields are not.
6. **Treat the response as authoritative.** Always read back the
   `etag` from `kv_set` / `files_finalize` — never assume your client
   already knows it.

## Worked example: caching an OCR result

```ts
// 1. Try cache first
const got = await rpc("storage/kv_get", {
  scope: "tool",
  key: `ocr/${sha}`,
  storage_token,
});

if (got.found) return got.value;

// 2. Run expensive OCR
const text = await runOCR(imageBytes);

// 3. Cache for 30 days
await rpc("storage/kv_set", {
  scope: "tool",
  key: `ocr/${sha}`,
  value: { text, ranAt: Date.now() },
  ttl_seconds: 30 * 24 * 3600,
  storage_token,
});

return text;
```

## Built-in user-storage tools

For LangChain agents, Nexus auto-registers six high-level wrappers
under the `user_storage_*` namespace
(`user_storage_get` / `user_storage_set` / `user_storage_delete` /
`user_storage_list` / `user_storage_files_save_text` /
`user_storage_files_get_url`). The agent never sees the raw RPC; the
tool wraps each call in a soft 5-write / 20-read per-invocation budget
and returns the same JSON envelope as above.

Users may disable the built-in tools per-account via
`UserSettings.disable_user_storage_tools`.

## See also

- [matrix-nexus design doc](https://github.com/talentai/matrix-nexus/blob/main/docs/design/anna-persistent-storage.md)
- [protocol-spec.md](protocol-spec.md) — full JSON-RPC envelope.
- [authorization.md](authorization.md) — how grants flow at runtime.
- [`examples/python/storage-notebook/`](../examples/python/storage-notebook/) — runnable Python plugin demonstrating reverse `storage/*` + `files/*` (KV with optimistic ETag retries plus two-step object uploads via the host's presigned URL).
