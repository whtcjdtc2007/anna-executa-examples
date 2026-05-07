中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Go Executa Plugin Examples

## Overview

This directory contains three standalone Go Executa plugin examples. Each file is self-contained and can be built/run independently:

| Example | File | Description |
|---------|------|-------------|
| **Basic Plugin** | `main.go` | System information queries, hash computation, string utilities |
| **Credential Plugin** | `credential_plugin.go` | Notion query tool, demonstrating credential (API Key) declaration and platform authorization integration |
| **Google OAuth Plugin** | `google_oauth_plugin.go` | Google Drive browser, demonstrating Google OAuth credential consumption via platform authorization |
| **Sampling Plugin (v2)** | `sampling-tool/` | Summarizer that asks the host to perform an LLM completion via reverse `sampling/createMessage` (no API key required — host owns model selection, billing and quota). Lives in its own subdirectory with a separate `go.mod` that uses `replace` to consume the local Go SDK. See [docs/sampling.md](../../docs/sampling.md). |

> **Note:** Each file has its own `func main()`. Use `go run <file>.go` to run a specific plugin, not `go run .`.

Go natively compiles to standalone binaries with **zero dependencies** and cross-platform support, making it an ideal choice for Binary distribution.

## How to Run

### Run Directly

```bash
# Basic plugin (system info, hash, string tools)
go run main.go

# Credential plugin (Notion API Key)
NOTION_TOKEN=ntn_xxx go run credential_plugin.go

# Google OAuth plugin (Drive file browser)
GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go
```

Test the protocol:

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run main.go 2>/dev/null
```

### Build Binary

```bash
# Current platform (basic plugin)
go build -o dist/example-go-tool main.go

# Credential plugins
go build -o dist/notion-credential-tool credential_plugin.go
go build -o dist/google-drive-tool google_oauth_plugin.go

# Or use the script / Makefile
./build.sh
make build              # basic plugin only
make build-all-plugins  # all three plugins
```

## Multi-Platform Build

Cross-compilation is a first-class feature in Go, requiring no additional tools:

### Using Makefile (Recommended)

```bash
# Build for all 7 standard platforms
make all

# Build + package
make package

# Build + protocol test
make test
```

### Using Build Script

```bash
# All platforms
./build.sh --all

# Build + test
./build.sh --all --test

# Build + package
./build.sh --package
```

### Manual Cross-Compilation

```bash
# macOS Apple Silicon
GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-arm64 main.go

# macOS Intel
GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-x86_64 main.go

# Linux x86_64
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-x86_64 main.go

# Linux ARM64
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-aarch64 main.go

# Windows x86_64
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-windows-x86_64.exe main.go
```

> `-ldflags="-s -w"` strips debug information, reducing binary size by approximately 30%.

## Distribute to Anna

Go plugins are best suited for **Binary** distribution:

1. Build for all platforms: `make package`
2. Upload to GitHub Releases (`dist/packages/*.tar.gz` / `*.zip`)
3. Configure each platform's Binary URL in Anna Admin:

```
darwin-arm64    →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-darwin-arm64.tar.gz
linux-x86_64    →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-linux-x86_64.tar.gz
windows-x86_64  →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-windows-x86_64.zip
```

## File Descriptions

| File | Description |
|------|-------------|
| `main.go` | Basic plugin main program (complete implementation) |
| `credential_plugin.go` | Credential plugin example — API Key pattern (platform authorization integration) |
| `google_oauth_plugin.go` | Google OAuth plugin example — Drive browser via OAuth access token |
| `go.mod` | Go module definition |
| `Makefile` | Multi-platform build, test, and packaging |
| `build.sh` | One-click build script |

## Credential Plugin Example

`credential_plugin.go` demonstrates integration with Anna Nexus's platform authorization:

```go
// Declare credentials in Manifest (naming aligned with platform providers)
"credentials": []map[string]any{
    {
        "name":      "NOTION_TOKEN",  // Aligns with platform credential_mapping
        "sensitive":  true,
    },
},

// Three-tier priority credential reading
func getCredential(credentials map[string]any, name string, defaultValue string) string {
    // 1. context.credentials (platform-injected)
    // 2. os.Getenv (local development)
    // 3. defaultValue
}
```

Local development testing:

```bash
# Provide credentials via environment variables
NOTION_TOKEN=ntn_xxx go run credential_plugin.go

# Test invoke with credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"search_pages","arguments":{"query":"test"},"context":{"credentials":{"NOTION_TOKEN":"ntn_test"}}},"id":2}' | go run credential_plugin.go 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for details

## Google OAuth Plugin Example

`google_oauth_plugin.go` demonstrates consuming **OAuth2 access tokens** provided by the platform for Google Drive file browsing. The plugin does NOT manage the OAuth flow — Nexus handles everything.

### Key Difference from API Key Plugins

From the plugin's perspective, the code is **identical** — just read from `context.credentials`:

```go
// API Key plugin
"credentials": []map[string]any{{"name": "NOTION_TOKEN", ...}}

// OAuth plugin — auto-injected by platform
"credentials": []map[string]any{{"name": "GOOGLE_ACCESS_TOKEN", ...}}  // Maps to $access_token
```

### Local Development Testing

```bash
# Provide OAuth token via environment variable
GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go

# Test describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run google_oauth_plugin.go 2>/dev/null

# Test invoke with OAuth credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_files","arguments":{"max_results":5},"context":{"credentials":{"GOOGLE_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | go run google_oauth_plugin.go 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for the full OAuth flow

## Protocol Interaction Examples

```bash
BINARY=./dist/example-go-tool

# Get tool manifest
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | $BINARY 2>/dev/null

# Query system information
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"system_info","arguments":{}},"id":2}' | $BINARY 2>/dev/null

# Compute hash
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"hash_text","arguments":{"text":"hello","algorithm":"sha256"}},"id":3}' | $BINARY 2>/dev/null

# String operations
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"string_utils","arguments":{"text":"hello","operation":"reverse"}},"id":4}' | $BINARY 2>/dev/null
```

## Adding Your Own Tools

1. Add a tool definition in the `manifest`'s `tools` array
2. Implement the tool function (receives `map[string]any` parameters, returns `map[string]any` result)
3. Register it in the `handleInvoke` switch statement

```go
// 1. Add definition in manifest
{
    "name":        "my_tool",
    "description": "...",
    "parameters": []map[string]any{...},
}

// 2. Implement
func toolMyTool(args map[string]any) map[string]any {
    input, _ := args["input"].(string)
    return map[string]any{"result": input}
}

// 3. Register (in handleInvoke switch)
case "my_tool":
    result = toolMyTool(args)
```

## Using Host LLM Sampling (Executa v2)

`sampling-tool/` shows how a long-running plugin can ask the host (Anna)
to run an LLM completion on its behalf — the plugin never holds an API key
and never picks a model. Unlike the other Go examples it lives in its own
subdirectory with its own `go.mod` (uses a `replace` directive pointing at
`../../../sdk/go`) so it can import the local Sampling SDK.

Key ingredients (already wired in the example):

1. **v2 handshake.** Implement `initialize`; reply with
   `protocolVersion: "2.0"` and `client_capabilities: { sampling: {} }`.
2. **Manifest declaration.** Add `host_capabilities: ["llm.sample"]` to
   the manifest returned by `describe`, otherwise Nexus refuses with
   `-32008 not_negotiated`.
3. **Reverse RPC.** Use [`sdk/go/sampling`](../../sdk/go/sampling/sampling.go):

   ```go
   import sampling "github.com/anna-executa/sdk/go/sampling"

   client := sampling.New(nil) // wires stdin/stdout by default
   res, err := client.CreateMessage(sampling.CreateMessageRequest{
       Messages:     []sampling.Message{{Role: "user", Content: sampling.TextContent{Type: "text", Text: "Summarize…"}}},
       MaxTokens:    400,
       SystemPrompt: "You are a concise assistant.",
       // No ModelPreferences → host falls back to the user's preferred_model.
       Metadata:     map[string]any{"executa_invoke_id": invokeID},
   }, 60*time.Second)
   ```

4. **End-user grant.** The user must enable sampling for this Executa in
   Anna Admin (writes `sampling_grant.enabled = true`).

Full wire reference and error codes: [docs/sampling.md](../../docs/sampling.md).

## Advantages of Go

| Feature | Description |
|---------|-------------|
| Zero Dependencies | No runtime installation required after compilation |
| Small Size | Typical binary 5-15 MB (compared to Python 50-200 MB) |
| Cross-Compilation | Built-in support, just one command |
| Fast Startup | Millisecond-level cold start |
| Concurrency | Goroutines natively support parallel computation |
