中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Node.js Executa Plugin Examples

## Overview

This directory contains two complete Node.js Executa plugin examples:

| Example | File | Description |
|---------|------|-------------|
| **Basic Plugin** | `example_plugin.js` | JSON formatting, Base64 encoding/decoding, hash computation |
| **Credential Plugin** | `credential_plugin.js` | GitHub query tool, demonstrating credential (API Key) declaration and platform authorization integration |
| **Google OAuth Plugin** | `google_oauth_plugin.js` | Google Calendar manager, demonstrating Google OAuth credential consumption via platform authorization |
| **Sampling Plugin (v2)** | `sampling-tool.js` | Summarizer that asks the host to perform an LLM completion via reverse `sampling/createMessage` (no API key required — host owns model selection, billing and quota). See [docs/sampling.md](../../docs/sampling.md). |

## How to Run

### Run Directly (Development/Debugging)

```bash
node example_plugin.js
```

Test in another terminal:

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null
```

### Install Globally via npm

```bash
npm install -g .
example-node-tool
```

### Via npm Scripts

```bash
npm start         # Run the plugin
npm test          # Test describe
npm run build     # Build binary
npm run build:test  # Build and test
```

## Build as Standalone Binary

### pkg (Recommended, Simple and Universal)

```bash
# Build for current platform
./build_binary.sh

# Build for all platforms
./build_binary.sh --all

# Build and test
./build_binary.sh --test
```

### Node.js SEA (Node.js 20+, Official Solution)

```bash
./build_binary.sh --sea --test
```

> **Note:** SEA only supports building binaries for the current platform. For cross-compilation, use pkg.

### Manual Build with pkg

```bash
npx pkg example_plugin.js --targets node18-macos-arm64,node18-linux-x64 --output dist/example-node-tool
```

## Distribute to Anna

### npm Distribution

In Anna Admin:
- Distribution method: **npm**
- Package name: `example-node-tool`

### Binary Distribution

1. Build multi-platform binaries: `./build_binary.sh --all`
2. Package:
   ```bash
   cd dist
   tar czf example-node-tool-darwin-arm64.tar.gz example-node-tool-darwin-arm64
   tar czf example-node-tool-linux-x86_64.tar.gz example-node-tool-linux-x86_64
   ```
3. Upload to GitHub Releases / S3
4. Configure each platform's Binary URL in Anna Admin

### Local Distribution

Local runs the **same v2 install pipeline as Binary** (extract → `tools/{tool_id}/v{version}/` → atomic `current` symlink), reading the archive from the Agent's local filesystem instead of an HTTPS URL. No HTTP server needed.

Build a standalone binary first (via `pkg` or SEA), then archive it:

```bash
./build_binary.sh
cd dist && tar czf example-node-tool.tar.gz example-node-tool-*
```

Then in Anna Admin:
- Distribution method: **Local**
- Local Archive Path: `/abs/path/to/dist/example-node-tool.tar.gz`
- Version: `dev` (or any string)

## File Descriptions

| File | Description |
|------|-------------|
| `example_plugin.js` | Basic plugin main program |
| `credential_plugin.js` | Credential plugin example — API Key pattern (platform authorization integration) |
| `google_oauth_plugin.js` | Google OAuth plugin example — Calendar manager via OAuth access token |
| `package.json` | npm package configuration |
| `build_binary.sh` | One-click build script (pkg / SEA) |

## Credential Plugin Example

`credential_plugin.js` demonstrates integration with Anna Nexus's platform authorization:

```javascript
// Declare credentials in Manifest (naming aligned with platform providers)
credentials: [
  {
    name: "GITHUB_TOKEN",           // Aligns with platform credential_mapping
    display_name: "Personal Access Token",
    required: true,
    sensitive: true,
  },
]

// Read credentials in tool function (three-tier priority)
function toolGetRepo(args, credentials) {
  // 1. Platform unified / plugin-level credentials (Agent-injected)
  // 2. Environment variable fallback (local development)
  const token = getCredential(credentials, "GITHUB_TOKEN");
}
```

Local development testing:

```bash
# Provide credentials via environment variables
GITHUB_TOKEN=ghp_xxx node credential_plugin.js

# Test invoke with credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_repo","arguments":{"owner":"octocat","repo":"hello-world"},"context":{"credentials":{"GITHUB_TOKEN":"ghp_test"}}},"id":2}' | node credential_plugin.js 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for details

## Google OAuth Plugin Example

`google_oauth_plugin.js` demonstrates consuming **OAuth2 access tokens** provided by the platform for Google Calendar operations. The plugin does NOT manage the OAuth flow — Nexus handles authorization, token exchange, and auto-refresh.

### Key Difference from API Key Plugins

From the plugin's perspective, the code is **identical** — just read from `context.credentials`. The only difference is the credential name:

```javascript
// API Key plugin
credentials: [{ name: "GITHUB_TOKEN", ... }]

// OAuth plugin — auto-injected by platform
credentials: [{ name: "GOOGLE_ACCESS_TOKEN", ... }]  // Maps to Google OAuth $access_token
```

### Local Development Testing

```bash
# Provide OAuth token via environment variable
GOOGLE_ACCESS_TOKEN=ya29.xxx node google_oauth_plugin.js

# Test describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node google_oauth_plugin.js 2>/dev/null

# Test invoke with OAuth credentials
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_events","arguments":{"max_results":5},"context":{"credentials":{"GOOGLE_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | node google_oauth_plugin.js 2>/dev/null
```

> See [Platform Authorization Documentation](../../docs/authorization.md) for the full OAuth flow

## Protocol Interaction Examples

```bash
# Get tool manifest
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null

# Call json_format
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"json_format","arguments":{"json_string":"{\"a\":1}","indent":4}},"id":2}' | node example_plugin.js 2>/dev/null

# Call base64_encode
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"base64_encode","arguments":{"text":"hello world"}},"id":3}' | node example_plugin.js 2>/dev/null

# Call hash_text
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"hash_text","arguments":{"text":"hello","algorithm":"sha256"}},"id":4}' | node example_plugin.js 2>/dev/null
```

## Adding Your Own Tools

1. Add a tool definition in `MANIFEST.tools`
2. Implement the tool function (receives an `args` object, returns a result object)
3. Register it in `TOOL_DISPATCH`

```javascript
// 1. Define
{ name: "my_tool", description: "...", parameters: [...] }

// 2. Implement
function toolMyTool(args) {
  const { input } = args;
  return { result: "..." };
}

// 3. Register
TOOL_DISPATCH["my_tool"] = toolMyTool;
```

## Using Host LLM Sampling (Executa v2)

`sampling-tool.js` shows how a long-running plugin can ask the host (Anna)
to run an LLM completion on its behalf — the plugin never holds an API key
and never picks a model.

Key ingredients (already wired in the example):

1. **v2 handshake.** Implement `initialize`; reply with
   `protocolVersion: "2.0"` and `client_capabilities: { sampling: {} }`.
2. **Manifest declaration.** Add `host_capabilities: ["llm.sample"]` to
   the manifest returned by `describe`, otherwise Nexus refuses with
   `-32008 not_negotiated`.
3. **Reverse RPC.** Use the SDK in [`../../sdk/nodejs/sampling.js`](../../sdk/nodejs/sampling.js):

   ```javascript
   const { SamplingClient } = require("../../sdk/nodejs");

   const sampling = new SamplingClient({ writeFrame });
   const result = await sampling.createMessage({
     messages: [{ role: "user", content: { type: "text", text: "Summarize…" } }],
     maxTokens: 400,
     systemPrompt: "You are a concise assistant.",
     // No modelPreferences → host falls back to the user's preferred_model.
     metadata: { executa_invoke_id: invokeId },
   });
   ```

4. **End-user grant.** The user must enable sampling for this Executa in
   Anna Admin (writes `sampling_grant.enabled = true`).

Full wire reference and error codes: [docs/sampling.md](../../docs/sampling.md).
