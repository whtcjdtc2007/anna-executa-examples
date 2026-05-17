# focus-session — Go flavour

Go implementation of `focus-session`. Behaviour is identical to
[`../focus-session-python`](../focus-session-python/): same
`~/.anna/focus-flow/state.json`, same `session` tool, same `action`
values.

中文版：[README.zh-CN.md](./README.zh-CN.md)

## When to choose this flavour

- You need a single-file, zero-runtime-dependency binary distribution
  (see "Build a binary" below).
- You want to verify that "the same Anna App bundle works seamlessly
  against Python / Node / Go executas".
- You want to exercise `anna-app dev`'s `go run` / `binary` launch
  paths.

> ⚠️ The App's `manifest.json` declares only **one** `tool_id`, so only
> **one** language flavour of `focus-session` can be enabled at a time.
> When `anna-app dev` discovers a duplicate `tool_id` it skips the later
> implementation and prints a warning.

## Enabling this flavour (dev mode: `go run`)

1. In this directory's [executa.json](./executa.json) flip `enabled` to `true`.
2. In [`../focus-session-python/executa.json`](../focus-session-python/executa.json) flip `enabled` to `false`.
3. Make sure `go version` ≥ 1.21. This plugin only uses the Go standard
   library, so no `go get` is required.
4. Back at the App root, run `anna-app dev`.

`autoDiscoverExecutas`'s default launch command is equivalent to:

```bash
cd executas/focus-session-go && go run .
```

The first launch triggers a compile; Go caches the build artifact, so
subsequent launches are nearly instant.

## Build a binary + switch to `binary` mode

```bash
cd executas/focus-session-go
mkdir -p bin
go build -o bin/tool-test-focus-session-12345678 .
```

Then change `executa.json` to:

```json
{
  "tool_id": "tool-test-focus-session-12345678",
  "type": "binary",
  "enabled": true
}
```

`anna-app dev` will now launch `bin/tool-test-focus-session-12345678`
directly, with no `go` toolchain required — this is also the shape of a
real distribution; see
[`anna-executa-examples/docs/binary-distribution.md`](../../../../docs/binary-distribution.md).

You can also override via the CLI flag:

```bash
anna-app dev --executa dir=./executas/focus-session-go,type=binary
# or
anna-app dev --executa dir=./executas/focus-session-go,type=go
```

## Protocol contract

- Single tool: `session(action, duration_minutes?, topic?, notes?)`
- `action ∈ {start, pause, resume, complete, get_state}`
- Returns the InvokeResult envelope `{success, data}` / `{success:false, error}`
- State file path is identical to the Python / Node flavours —
  switching languages does not lose history.

For the full API and design trade-offs see
[`../focus-session-python/README.md`](../focus-session-python/README.md).
