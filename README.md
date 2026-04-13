> 中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Anna Executa Plugin Examples

This repository provides **complete examples and development documentation** for Anna Executa plugins, covering Python, Node.js, and Go, with both Local and Binary distribution methods.

## What is Executa?

Executa is the plugin extension system for Anna Agent. Developers can write tools in **any programming language** — as long as they implement the standard **JSON-RPC 2.0 over stdio** protocol, Anna will automatically discover, load, and expose them to the LLM.

## Directory Structure

```
anna-executa-examples/
├── docs/                       # Development documentation
│   ├── protocol-spec.md        # Protocol specification
│   ├── authorization.md        # Platform authorization guide
│   └── binary-distribution.md  # Binary distribution guide
├── examples/
│   ├── python/                 # Python plugin examples
│   │   ├── example_plugin.py       # Basic plugin (text processing)
│   │   ├── credential_plugin.py    # Credential plugin (Weather API)
│   │   ├── pyproject.toml
│   │   ├── build_binary.sh
│   │   └── README.md
│   ├── nodejs/                 # Node.js plugin examples
│   │   ├── example_plugin.js       # Basic plugin (JSON/Base64/Hash)
│   │   ├── credential_plugin.js    # Credential plugin (GitHub API)
│   │   ├── package.json
│   │   ├── build_binary.sh
│   │   └── README.md
│   └── go/                     # Go plugin examples
│       ├── main.go                 # Basic plugin (System info/Hash)
│       ├── credential_plugin.go    # Credential plugin (Notion API)
│       ├── go.mod
│       ├── build.sh
│       ├── Makefile
│       └── README.md
└── .github/
    └── workflows/
        └── build-release.yml   # Multi-platform CI/CD example
```

## Quick Start

### Python Plugin

```bash
cd examples/python

# Run directly
python example_plugin.py

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# Build as a standalone binary
./build_binary.sh --test
```

### Node.js Plugin

```bash
cd examples/nodejs

# Run directly
node example_plugin.js

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null

# Build as a standalone binary (requires Node.js 18+)
./build_binary.sh --test
```

### Go Plugin

```bash
cd examples/go

# Run directly
go run .

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run . 2>/dev/null

# Build a native binary
go build -o dist/example-go-tool .

# Build binaries for all platforms
make all
```

### Credential Plugins (Platform Authorization)

Each language includes a credential plugin example demonstrating how to declare and use platform-managed credentials:

```bash
# Python — Weather query (requires WEATHER_API_KEY)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python examples/python/credential_plugin.py 2>/dev/null

# Provide credentials via environment variables for local development
WEATHER_API_KEY=your_key python examples/python/credential_plugin.py

# Node.js — GitHub query (requires GITHUB_TOKEN)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/credential_plugin.js 2>/dev/null

# Go — Notion query (requires NOTION_TOKEN)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/credential_plugin.go 2>/dev/null
```

## Distribution Methods

| Method | Installation | Use Case |
|--------|-------------|----------|
| **uv** | `uv tool install <package>` | Python tools (recommended) |
| **pipx** | `pipx install <package>` | Python tools |
| **npm** | `npm install -g <package>` | Node.js tools |
| **Homebrew** | `brew install <formula>` | macOS / Linux |
| **Binary** | HTTP download | Pre-built binaries (any language) |
| **Local** | Local path | Development & debugging |

## Documentation

- [Protocol Specification](docs/protocol-spec.md) — Full JSON-RPC 2.0 over stdio protocol definition
- [Platform Authorization](docs/authorization.md) — Credential declaration, auto-injection, and platform authorization integration
- [Binary Distribution Guide](docs/binary-distribution.md) — Building, signing, and multi-platform deployment

## License

MIT
