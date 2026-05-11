> 中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Anna Executa Plugin Examples

This repository provides **complete examples and development documentation** for Anna Executa plugins, covering Python, Node.js, and Go, with both Local and Binary distribution methods.

## What is Executa?

Executa is the plugin extension system for Anna Agent. Developers can write tools in **any programming language** — as long as they implement the standard **JSON-RPC 2.0 over stdio** protocol, Anna will automatically discover, load, and expose them to the LLM.

## Directory Structure

```
anna-executa-examples/
├── docs/                                # Development documentation
│   ├── protocol-spec.md                 # Protocol specification
│   ├── authorization.md                 # Platform authorization guide
│   ├── binary-distribution.md           # Binary distribution guide
│   ├── sampling.md                      # Reverse LLM sampling (sampling/createMessage)
│   ├── persistent-storage.md            # Anna Persistent Storage (storage/* + files/*)
│   └── common-pitfalls.md               # ⚠️ Read this first if your plugin shows as "Stopped"
├── examples/
│   ├── python/                          # Python plugin examples (each in its own subdir)
│   │   ├── basic-tool/                  # Basic plugin (text processing)
│   │   ├── credential-tool/             # Credential plugin (Weather API Key)
│   │   ├── google-oauth-tool/           # Google OAuth plugin (Gmail)
│   │   ├── sampling-summarizer/         # Sampling plugin v2 (reverse sampling/createMessage)
│   │   ├── storage-notebook/            # Persistent Storage plugin v2 (reverse storage/* + files/*)
│   │   └── build_binary.sh              # Builds all examples (or one) via PyInstaller
│   ├── nodejs/                          # Node.js plugin examples
│   │   ├── example_plugin.js            # Basic plugin (JSON/Base64/Hash)
│   │   ├── credential_plugin.js         # Credential plugin (GitHub API Key)
│   │   ├── google_oauth_plugin.js       # Google OAuth plugin (Calendar)
│   │   ├── sampling-tool.js             # Sampling plugin v2
│   │   └── build_binary.sh
│   ├── go/                              # Go plugin examples
│   │   ├── main.go                      # Basic plugin (system info / hash)
│   │   ├── credential_plugin.go         # Credential plugin (Notion API Key)
│   │   ├── google_oauth_plugin.go       # Google OAuth plugin (Drive)
│   │   ├── sampling-tool/               # Sampling plugin v2 (separate go.mod)
│   │   ├── build.sh
│   │   └── Makefile
│   ├── multifile-binary/                # Multi-file Binary distribution examples
│   │   └── python-pyinstaller-onedir/   # PyInstaller --onedir + manifest.json
│   └── anna-app-focus-flow/             # ⭐ Complete Anna App (tool + skill + UI bundle + manifest)
├── sdk/                                 # Reference SDKs used by the sampling examples
│   ├── python/                          # executa_sdk
│   ├── nodejs/                          # @anna/executa-sdk
│   └── go/                              # github.com/anna/executa-sdk
└── .github/
    └── workflows/
        ├── build-release.yml            # Multi-platform CI/CD example
        └── anna-app.yml                 # Anna App packaging workflow
```

## Quick Start

### Python Plugin

Each Python example is a self-contained subdirectory with its own `pyproject.toml` and PyInstaller spec.

```bash
cd examples/python/basic-tool

# Run directly
python example_plugin.py

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# Install via uv
uv tool install . && example-text-tool

# Build as a standalone binary (from examples/python/, builds all subdirs or one)
cd .. && ./build_binary.sh --test
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

# Run directly (each plugin has its own func main — pass the file explicitly)
go run main.go

# Test the protocol
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run main.go 2>/dev/null

# Build a native binary
go build -o dist/example-go-tool main.go

# Build binaries for all platforms / all plugins
make all
```

### Credential Plugins — API Key Pattern

Each language includes a credential plugin example demonstrating how to declare and use platform-managed credentials:

```bash
# Python — Weather query (requires WEATHER_API_KEY)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | \
  python examples/python/credential-tool/credential_plugin.py 2>/dev/null

# Provide credentials via environment variables for local development
WEATHER_API_KEY=your_key python examples/python/credential-tool/credential_plugin.py

# Node.js — GitHub query (requires GITHUB_TOKEN)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/credential_plugin.js 2>/dev/null

# Go — Notion query (requires NOTION_TOKEN)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/credential_plugin.go 2>/dev/null
```

### Google OAuth Plugins — OAuth2 Token Pattern

Each language also includes a Google OAuth plugin example showing how to consume OAuth access tokens injected by the platform. From the plugin's perspective, the API is identical to API Key — the platform handles all OAuth complexity (authorization, token exchange, auto-refresh):

```bash
# Python — Gmail read (requires GMAIL_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | \
  python examples/python/google-oauth-tool/google_oauth_plugin.py 2>/dev/null

# Node.js — Google Calendar (requires GOOGLE_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/google_oauth_plugin.js 2>/dev/null

# Go — Google Drive (requires GOOGLE_ACCESS_TOKEN via Google OAuth)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/google_oauth_plugin.go 2>/dev/null

# Local development — provide token via env var
GOOGLE_ACCESS_TOKEN=ya29.xxx node examples/nodejs/google_oauth_plugin.js
```

### Sampling Plugins (v2) — Reverse `sampling/createMessage`

Plugins can ask the host to perform an LLM completion on their behalf. The host owns model selection, billing and quota — the plugin needs no API key. See [docs/sampling.md](docs/sampling.md).

```bash
# Python
python examples/python/sampling-summarizer/sampling_summarizer.py

# Node.js
node examples/nodejs/sampling-tool.js

# Go (separate go.mod inside the subdirectory)
cd examples/go/sampling-tool && go run ./...
```

### Persistent Storage Plugins (v2) — Reverse `storage/*` + `files/*`

Plugins can persist per-user / per-app state and upload binary
attachments without holding any cloud-storage credential — Anna owns the
bucket, encryption, quota and per-app ACL. See
[docs/persistent-storage.md](docs/persistent-storage.md).

```bash
# Python
python examples/python/storage-notebook/storage_notebook.py
```

## Distribution Methods

| Method | Installation | Use Case |
|--------|-------------|----------|
| **uv** | `uv tool install <package>` | Python tools (recommended) |
| **pipx** | `pipx install <package>` | Python tools |
| **npm** | `npm install -g <package>` | Node.js tools |
| **Homebrew** | `brew install <formula>` | macOS / Linux |
| **Binary** | HTTP download | Pre-built binaries (any language) |
| **Local** | Local archive on Agent host (`.tar.gz`/`.tgz`/`.zip` or raw exe) | Dev iteration, internal/air-gapped distribution — same install pipeline as Binary, supports multi-file binaries (see [`examples/multifile-binary/`](examples/multifile-binary/)) |

## Documentation

- [Protocol Specification](docs/protocol-spec.md) — Full JSON-RPC 2.0 over stdio protocol definition
- [Platform Authorization](docs/authorization.md) — Credential declaration, auto-injection, and platform authorization integration
- [Binary Distribution Guide](docs/binary-distribution.md) — Building, signing, and multi-platform deployment
- [Reverse Sampling](docs/sampling.md) — Plugins requesting LLM completions from the host
- [Persistent Storage](docs/persistent-storage.md) — Per-user / per-app KV + object storage hosted by Anna
- [Common Pitfalls](docs/common-pitfalls.md) — Read this first when a plugin shows as "Stopped"
- [Anna App Example — Focus Flow](examples/anna-app-focus-flow/README.md) — End-to-end Anna App: 1 tool + 1 skill + premium UI bundle + full app manifest

## License

MIT
