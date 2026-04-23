中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Python Executa Plugin Examples

## Overview

This directory contains two complete Python Executa plugin examples:

| Example | File | Description |
|---------|------|-------------|
| **Basic Plugin** | `example_plugin.py` | Text processing toolkit (word_count, text_transform, batch_word_count) |
| **Credential Plugin** | `credential_plugin.py` | Weather query tool, demonstrating credential (API Key) declaration and platform authorization integration |
| **Google OAuth Plugin** | `google_oauth_plugin.py` | Gmail reader, demonstrating Google OAuth credential consumption via platform authorization |

## How to Run

### Run Directly (Development/Debugging)

```bash
python example_plugin.py
```

Test in another terminal:

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null
```

### Install via uv (Recommended Local Distribution Method)

```bash
# Install as a global tool
uv tool install .

# Run
example-text-tool
```

### Install via pipx

```bash
pipx install .
example-text-tool
```

## Build as Standalone Binary

### PyInstaller (Recommended)

```bash
# One-click build
./build_binary.sh

# Build and test
./build_binary.sh --test
```

### Nuitka (Smaller Size)

```bash
./build_binary.sh --nuitka --test
```

### Manual Build

```bash
pip install pyinstaller

# Using spec file
pyinstaller example-text-tool.spec

# Or command line
pyinstaller --onefile --name example-text-tool --strip --noupx example_plugin.py
```

## Distribute to Anna

### Local Distribution

Local runs the **same v2 install pipeline as Binary** (extract → `tools/{tool_id}/v{version}/` → atomic `current` symlink → `bin/{name}` shim), but reads the archive from the Agent's local filesystem instead of an HTTPS URL. Multi-file archives (PyInstaller `--onedir` with bundled `.so` / `_internal/`) are fully supported — see `examples/multifile-binary/python-pyinstaller-onedir/`.

Build a binary first, then archive it:

```bash
./build_binary.sh
cd dist && tar czf example-text-tool.tar.gz example-text-tool
```

Then in Anna Admin:
- Distribution method: **Local**
- Local Archive Path: `/abs/path/to/dist/example-text-tool.tar.gz`
- Version: `dev` (or any string)
- Protocol: `stdio`

### Binary Distribution

1. Build binary: `./build_binary.sh`
2. Package: `cd dist && tar czf example-text-tool-darwin-arm64.tar.gz example-text-tool`
3. Upload to GitHub Releases / S3 / any HTTP service
4. Configure the Binary URL in Anna Admin

### uv Distribution

In Anna Admin:
- Distribution method: **uv**
- Package name: `example-text-tool` (or PyPI package name)

## File Descriptions

| File | Description |
|------|-------------|
| `example_plugin.py` | Basic plugin main program (can be run directly) |
| `credential_plugin.py` | Credential plugin example — API Key pattern (platform authorization integration) |
| `google_oauth_plugin.py` | Google OAuth plugin example — Gmail reader via OAuth access token |
| `pyproject.toml` | Python package configuration (required for uv/pipx installation) |
| `build_binary.sh` | One-click build script (PyInstaller / Nuitka) |
| `example-text-tool.spec` | PyInstaller configuration file |
| `weather-tool.spec` | Credential plugin PyInstaller configuration file |

## Credential Plugin Example

`credential_plugin.py` demonstrates integration with Anna Nexus's platform authorization:

### Credential Declaration

Declare required credentials in the Manifest's `credentials` field — naming aligned with platform providers enables automatic mapping:

```python
"credentials": [
    {
        "name": "WEATHER_API_KEY",       # Aligns with platform credential_mapping
        "display_name": "API Key",        # UI display name
        "required": True,
        "sensitive": True,                # Encrypted storage, not echoed in UI
    },
]
```

### Credential Reading (Three-Tier Priority)

```python
def tool_get_weather(city: str, *, credentials: dict | None = None) -> dict:
    creds = credentials or {}
    # 1. Platform unified / plugin-level credentials (Agent-injected)
    api_key = creds.get("WEATHER_API_KEY")
    # 2. Environment variable fallback (local development)
    if not api_key:
        api_key = os.environ.get("WEATHER_API_KEY")
```

### Local Development Testing

```bash
# Provide credentials via environment variables
WEATHER_API_KEY=your_key python credential_plugin.py

# Test describe (view credential declarations)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python credential_plugin.py 2>/dev/null

# Test invoke with credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_weather","arguments":{"city":"Beijing"},"context":{"credentials":{"WEATHER_API_KEY":"test_key"}}},"id":2}' | python credential_plugin.py 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for details

## Google OAuth Plugin Example

`google_oauth_plugin.py` demonstrates consuming **OAuth2 access tokens** provided by the platform. Unlike API Key credentials, the plugin does NOT manage the OAuth flow — the platform handles authorization, token exchange, and auto-refresh.

### Key Difference from API Key Plugins

From the plugin's perspective, the code is **identical** — just read from `context.credentials`. The only difference is naming alignment:

```python
# API Key plugin — custom service credential
"credentials": [{"name": "WEATHER_API_KEY", ...}]

# OAuth plugin — platform provider credential (auto-injected)
"credentials": [{"name": "GMAIL_ACCESS_TOKEN", ...}]  # Maps to Google OAuth $access_token
```

### Local Development Testing

```bash
# Provide OAuth token via environment variable
GMAIL_ACCESS_TOKEN=ya29.xxx python google_oauth_plugin.py

# Test describe (view OAuth credential declarations)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python google_oauth_plugin.py 2>/dev/null

# Test invoke with OAuth credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_messages","arguments":{"query":"is:unread","max_results":5},"context":{"credentials":{"GMAIL_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | python google_oauth_plugin.py 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for the full OAuth flow

## Protocol Interaction Examples

```bash
# Get tool manifest
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# Call word_count
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"word_count","arguments":{"text":"hello world"}},"id":2}' | python example_plugin.py 2>/dev/null

# Call text_transform
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"text_transform","arguments":{"text":"hello","transform":"upper"}},"id":3}' | python example_plugin.py 2>/dev/null

# Health check
echo '{"jsonrpc":"2.0","method":"health","id":4}' | python example_plugin.py 2>/dev/null
```

## Adding Your Own Tools

1. Add a tool definition in `MANIFEST["tools"]` (name, description, parameters)
2. Implement the tool function
3. Register it in the `TOOL_DISPATCH` dictionary

```python
# 1. Define
{"name": "my_tool", "description": "...", "parameters": [...]}

# 2. Implement
def tool_my_tool(arg1: str, arg2: int = 10) -> dict:
    return {"result": "..."}

# 3. Register
TOOL_DISPATCH["my_tool"] = tool_my_tool
```
