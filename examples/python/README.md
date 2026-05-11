中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Python Executa Plugin Examples

## Overview

Each example is a **self-contained subdirectory** with its own
`pyproject.toml`, plugin source, and PyInstaller spec. This mirrors the
Go `sampling-tool/` layout and lets each example be installed,
distributed, or built in isolation.

| Example | Subdirectory | Description |
|---------|--------------|-------------|
| **Basic Plugin** | [`basic-tool/`](basic-tool/) | Text processing toolkit (`word_count`, `text_transform`, `batch_word_count`). |
| **Credential Plugin** | [`credential-tool/`](credential-tool/) | Weather query tool — declares an API-Key credential and consumes it via the platform's unified authorization. |
| **Google OAuth Plugin** | [`google-oauth-tool/`](google-oauth-tool/) | Gmail reader — consumes Google OAuth2 access tokens injected by the platform (no OAuth flow inside the plugin). |
| **Sampling Plugin (v2)** | [`sampling-summarizer/`](sampling-summarizer/) | Summarizer that asks the host to perform an LLM completion via reverse `sampling/createMessage` (no API key required — host owns model selection, billing and quota). See [docs/sampling.md](../../docs/sampling.md). |
| **Storage Plugin (v2)** | [`storage-notebook/`](storage-notebook/) | Notebook + attachment uploader that uses Anna Persistent Storage via reverse `storage/*` and `files/*` RPC (per-user/app KV plus two-step object uploads via the host's presigned URL). See [docs/persistent-storage.md](../../docs/persistent-storage.md). |

## Subdirectory Layout

Every example follows the same convention:

```
<example-subdir>/
  pyproject.toml          # name, scripts, build config
  <plugin>.py             # the plugin source — single file, self-contained
  pyinstaller.spec        # PyInstaller --onefile config (binary name)
```

Shared at the top level:

```
examples/python/
  README.md / README.zh-CN.md
  build_binary.sh         # builds ALL examples, or one when invoked with a subdir arg
```

## How to Run

### Run Directly (Development / Debugging)

```bash
cd basic-tool
python example_plugin.py

# In another terminal
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null
```

The other examples follow the same pattern — `cd <subdir>` and run the
single `.py` file. Each subdirectory's plugin file lists the tool name
in its `MANIFEST` constant near the top.

### Install via uv (Recommended Local Distribution Method)

Each subdirectory is its own installable package. Install whichever you
need; they do not collide.

```bash
cd basic-tool && uv tool install . && example-text-tool
cd credential-tool && uv tool install . && weather-tool
cd google-oauth-tool && uv tool install . && gmail-tool
cd sampling-summarizer && uv tool install . && sampling-summarizer
cd storage-notebook && uv tool install . && storage-notebook
```

### Install via pipx

```bash
cd basic-tool && pipx install . && example-text-tool
```

## Build as Standalone Binary

The shared `build_binary.sh` lives at this directory's root. It detects
plugin subdirectories (those with a `pyproject.toml` and at least one
`*.py`) and dispatches the build into each.

### Build all examples

```bash
./build_binary.sh                  # PyInstaller --onefile, all subdirs
./build_binary.sh --test           # …also run protocol smoke tests
./build_binary.sh --nuitka --test  # use Nuitka instead
```

### Build a specific example

From the root, name one or more subdirectories:

```bash
./build_binary.sh basic-tool
./build_binary.sh credential-tool sampling-summarizer --test
```

…or invoke the script from inside the subdirectory:

```bash
cd sampling-summarizer
../build_binary.sh --test
```

Output: `<subdir>/dist/<plugin-name>` (one single-file executable per
plugin).

### Manual build

```bash
cd basic-tool
pip install pyinstaller

# Using the spec file
pyinstaller pyinstaller.spec

# Or by command line
pyinstaller --onefile --name example-text-tool --strip --noupx example_plugin.py
```

## Distribute to Anna

### Local Distribution

Local runs the **same v2 install pipeline as Binary** (extract →
`tools/{tool_id}/v{version}/` → atomic `current` symlink → `bin/{name}`
shim), but reads the archive from the Agent's local filesystem instead
of an HTTPS URL. Multi-file archives (PyInstaller `--onedir` with
bundled `.so` / `_internal/`) are fully supported — see
[`examples/multifile-binary/python-pyinstaller-onedir/`](../multifile-binary/python-pyinstaller-onedir/).

```bash
cd basic-tool
../build_binary.sh basic-tool
cd dist && tar czf example-text-tool.tar.gz example-text-tool
```

Then in Anna Admin:
- Distribution method: **Local**
- Local Archive Path: `/abs/path/to/dist/example-text-tool.tar.gz`
- Version: `dev` (or any string)
- Protocol: `stdio`

### Binary Distribution

1. Build a binary inside the example subdirectory (see above).
2. Package: `cd <subdir>/dist && tar czf <name>-darwin-arm64.tar.gz <name>`.
3. Upload to GitHub Releases / S3 / any HTTP service.
4. Configure the Binary URL in Anna Admin.

### uv Distribution

In Anna Admin:
- Distribution method: **uv**
- Package name: the `name` declared in that subdirectory's `pyproject.toml`
  (e.g. `example-text-tool`, `weather-tool`, `gmail-tool`,
  `sampling-summarizer`, `storage-notebook`).

## Credential Plugin Example

[`credential-tool/credential_plugin.py`](credential-tool/credential_plugin.py)
demonstrates integration with Anna Nexus's platform authorization.

### Credential Declaration

Declare required credentials in the Manifest's `credentials` field —
naming aligned with platform providers enables automatic mapping:

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
cd credential-tool

# Provide credentials via environment variables
WEATHER_API_KEY=your_key python credential_plugin.py

# Test describe (view credential declarations)
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python credential_plugin.py 2>/dev/null

# Test invoke with credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_weather","arguments":{"city":"Beijing"},"context":{"credentials":{"WEATHER_API_KEY":"test_key"}}},"id":2}' | python credential_plugin.py 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for details

## Google OAuth Plugin Example

[`google-oauth-tool/google_oauth_plugin.py`](google-oauth-tool/google_oauth_plugin.py)
demonstrates consuming **OAuth2 access tokens** provided by the
platform. Unlike API Key credentials, the plugin does NOT manage the
OAuth flow — the platform handles authorization, token exchange, and
auto-refresh.

### Key Difference from API Key Plugins

From the plugin's perspective, the code is **identical** — just read
from `context.credentials`. The only difference is naming alignment:

```python
# API Key plugin — custom service credential
"credentials": [{"name": "WEATHER_API_KEY", ...}]

# OAuth plugin — platform provider credential (auto-injected)
"credentials": [{"name": "GMAIL_ACCESS_TOKEN", ...}]  # Maps to Google OAuth $access_token
```

### Local Development Testing

```bash
cd google-oauth-tool

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
cd basic-tool

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

Pick the example closest to what you want to build (basic / credential /
OAuth / sampling / storage), copy its subdirectory, then:

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

If your new example needs its own published name, also update the
subdirectory's `pyproject.toml` (`[project] name`, `[project.scripts]`)
and rename the binary in `pyinstaller.spec`.

## Using Host LLM Sampling (Executa v2)

[`sampling-summarizer/sampling_summarizer.py`](sampling-summarizer/sampling_summarizer.py)
shows how a long-running plugin can ask the host (Anna) to run an LLM
completion on its behalf — the plugin never holds an API key and never
picks a model.

Key ingredients (already wired in the example):

1. **v2 handshake.** Implement `initialize`; reply with
   `protocolVersion: "2.0"` and `client_capabilities: { sampling: {} }`.
2. **Manifest declaration.** Add `host_capabilities: ["llm.sample"]` to
   the manifest returned by `describe`, otherwise Nexus refuses with
   `-32008 not_negotiated`.
3. **Reverse RPC.** Use the SDK in [`../../sdk/python/executa_sdk/sampling.py`](../../sdk/python/executa_sdk/sampling.py):

   ```python
   from executa_sdk import SamplingClient

   sampling = SamplingClient(write_frame=write_frame)  # write_frame = your stdout writer
   result = await sampling.create_message(
       messages=[{"role": "user", "content": {"type": "text", "text": "Summarize…"}}],
       max_tokens=400,
       system_prompt="You are a concise assistant.",
       # No model_preferences → host falls back to the user's preferred_model.
       metadata={"executa_invoke_id": invoke_id},
   )
   ```

4. **End-user grant.** The user must enable sampling for this Executa in
   Anna Admin (writes `sampling_grant.enabled = true`).

Full wire reference and error codes: [docs/sampling.md](../../docs/sampling.md).

## Using Persistent Storage (Executa v2)

[`storage-notebook/storage_notebook.py`](storage-notebook/storage_notebook.py)
shows how a plugin can persist per-user/app state and upload binary
attachments without ever holding cloud-storage credentials of its own —
Anna owns the bucket, the encryption, the quota, and the per-app ACL.

Key ingredients (already wired in the example):

1. **v2 handshake.** Implement `initialize`; reply with
   `protocolVersion: "2.0"` and
   `client_capabilities: { storage: { kv: true, files: true } }`.
2. **Manifest declaration.** Declare the host capabilities you need
   (`aps.kv`, `aps.files` — or the `storage.user` / `storage.app` /
   `storage.tool` scope you intend to use). Without this Nexus refuses
   with `-32008 not_negotiated`.
3. **Reverse RPC.** Use the SDK in [`../../sdk/python/executa_sdk/storage.py`](../../sdk/python/executa_sdk/storage.py)
   and [`files.py`](../../sdk/python/executa_sdk/files.py):

   ```python
   from executa_sdk import StorageClient, FilesClient, make_response_router

   storage = StorageClient()
   files = FilesClient()
   route_response = make_response_router(storage, files)  # multiplex stdin

   # Optimistic-concurrency KV write
   cur = await storage.get("notes/log")
   await storage.set("notes/log", new_value, if_match=cur.get("etag"))

   # Two-step file upload (presigned PUT → finalize)
   info = await files.upload_begin(path="attachments/x.txt", size_bytes=N,
                                   content_type="text/plain")
   # …PUT bytes to info["put_url"]…
   await files.upload_complete(path="attachments/x.txt", etag=etag, size_bytes=N)
   ```

4. **End-user grant.** The user must enable persistent storage for
   this Executa in Anna Admin (writes
   `storage_grant.scopes = ["user", …]` plus quota overrides).

Full wire reference, error codes and quota semantics:
[docs/persistent-storage.md](../../docs/persistent-storage.md).
