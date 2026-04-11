中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Python Executa Plugin Example

## Overview

This is a complete Python Executa plugin example that implements a text processing toolkit, including three tools: `word_count`, `text_transform`, and `text_repeat`.

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

In Anna Admin:
- Distribution method: **Local**
- Path: enter the Python script path, e.g. `/path/to/example_plugin.py`
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
| `example_plugin.py` | Plugin main program (can be run directly) |
| `pyproject.toml` | Python package configuration (required for uv/pipx installation) |
| `build_binary.sh` | One-click build script (PyInstaller / Nuitka) |
| `example-text-tool.spec` | PyInstaller configuration file |

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
