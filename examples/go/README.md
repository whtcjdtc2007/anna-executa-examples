中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Go Executa Plugin Example

## Overview

This is a complete Go Executa plugin example that implements system information queries, hash computation, and string utility tools.

Go natively compiles to standalone binaries with **zero dependencies** and cross-platform support, making it an ideal choice for Binary distribution.

## How to Run

### Run Directly

```bash
go run .
```

Test the protocol:

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run . 2>/dev/null
```

### Build Binary

```bash
# Current platform
go build -o dist/example-go-tool .

# Or use the script
./build.sh

# Or use Makefile
make build
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
GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-arm64 .

# macOS Intel
GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-x86_64 .

# Linux x86_64
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-x86_64 .

# Linux ARM64
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-aarch64 .

# Windows x86_64
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-windows-x86_64.exe .
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
| `main.go` | Plugin main program (complete implementation) |
| `go.mod` | Go module definition |
| `Makefile` | Multi-platform build, test, and packaging |
| `build.sh` | One-click build script |

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

## Advantages of Go

| Feature | Description |
|---------|-------------|
| Zero Dependencies | No runtime installation required after compilation |
| Small Size | Typical binary 5-15 MB (compared to Python 50-200 MB) |
| Cross-Compilation | Built-in support, just one command |
| Fast Startup | Millisecond-level cold start |
| Concurrency | Goroutines natively support parallel computation |
