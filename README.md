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
│   ├── binary-distribution.md  # Binary distribution guide
│   └── common-pitfalls.md      # ⚠️ Read this first if your plugin shows as "Stopped"
├── examples/
│   ├── python/                 # Python plugin examples
│   │   ├── example_plugin.py       # Basic plugin (text processing)
│   │   ├── credential_plugin.py    # Credential plugin (Weather API Key)
│   │   ├── google_oauth_plugin.py  # Google OAuth plugin (Gmail)
│   │   ├── pyproject.toml
│   │   ├── build_binary.sh
│   │   └── README.md
│   ├── nodejs/                 # Node.js plugin examples
│   │   ├── example_plugin.js       # Basic plugin (JSON/Base64/Hash)
│   │   ├── credential_plugin.js    # Credential plugin (GitHub API Key)
│   │   ├── google_oauth_plugin.js  # Google OAuth plugin (Calendar)
│   │   ├── package.json
│   │   ├── build_binary.sh
│   │   └── README.md
│   ├── go/                     # Go plugin examples
│   │   ├── main.go                 # Basic plugin (System info/Hash)
│   │   ├── credential_plugin.go    # Credential plugin (Notion API Key)
│   │   ├── google_oauth_plugin.go  # Google OAuth plugin (Drive)
│   │   ├── go.mod
│   │   ├── build.sh
│   │   ├── Makefile
│   │   └── README.md
│   └── anna-app-focus-flow/    # ⭐ Complete Anna App example (tool + skill + UI bundle + manifest)
│       ├── manifest.json           # AppManifest schema:1
│       ├── app.json                # App metadata
│       ├── bundle/                 # Premium glassmorphism UI (HTML/CSS/JS)
│       ├── executas/focus-session/ # 1× Executa TOOL (Python stdio)
│       ├── executas/focus-coach/   # 1× Executa SKILL (SKILL.md)
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
go run main.go

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run main.go 2>/dev/null

# Build a native binary
go build -o dist/example-go-tool main.go

# Build binaries for all platforms
make all
```

### Credential Plugins — API Key Pattern

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

### Google OAuth Plugins — OAuth2 Token Pattern

Each language also includes a Google OAuth plugin example showing how to consume OAuth access tokens injected by the platform. From the plugin's perspective, the API is identical to API Key — the platform handles all OAuth complexity (authorization, token exchange, auto-refresh):

```bash
# Python — Gmail read (requires GMAIL_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python examples/python/google_oauth_plugin.py 2>/dev/null

# Node.js — Google Calendar (requires GOOGLE_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/google_oauth_plugin.js 2>/dev/null

# Go — Google Drive (requires GOOGLE_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/google_oauth_plugin.go 2>/dev/null

# Local development — provide token via env var
GOOGLE_ACCESS_TOKEN=ya29.xxx node examples/nodejs/google_oauth_plugin.js
```

## Distribution Methods

| Method | Installation | Use Case |
|--------|-------------|----------|
| **uv** | `uv tool install <package>` | Python tools (recommended) |
| **pipx** | `pipx install <package>` | Python tools |
| **npm** | `npm install -g <package>` | Node.js tools |
| **Homebrew** | `brew install <formula>` | macOS / Linux |
| **Binary** | HTTP download | Pre-built binaries (any language) |
| **Local** | Local archive on Agent host (`.tar.gz`/`.tgz`/`.zip` or raw exe) | Dev iteration, internal/air-gapped distribution — same install pipeline as Binary, supports multi-file binaries |

## Documentation

- [Protocol Specification](docs/protocol-spec.md) — Full JSON-RPC 2.0 over stdio protocol definition
- [Platform Authorization](docs/authorization.md) — Credential declaration, auto-injection, and platform authorization integration
- [Binary Distribution Guide](docs/binary-distribution.md) — Building, signing, and multi-platform deployment
- [Anna App Example — Focus Flow](examples/anna-app-focus-flow/README.md) — End-to-end Anna App: 1 tool + 1 skill + premium UI bundle + full app manifest

## License

MIT
