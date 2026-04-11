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
│   └── binary-distribution.md  # Binary distribution guide
├── examples/
│   ├── python/                 # Python plugin example
│   │   ├── example_plugin.py
│   │   ├── pyproject.toml
│   │   ├── build_binary.sh
│   │   ├── example-text-tool.spec
│   │   └── README.md
│   ├── nodejs/                 # Node.js plugin example
│   │   ├── example_plugin.js
│   │   ├── package.json
│   │   ├── build_binary.sh
│   │   └── README.md
│   └── go/                     # Go plugin example
│       ├── main.go
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
- [Binary Distribution Guide](docs/binary-distribution.md) — Building, signing, and multi-platform deployment

## License

MIT
