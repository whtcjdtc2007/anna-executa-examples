中文版本请参阅 [README.zh-CN.md](README.zh-CN.md)

# Node.js Executa Plugin Examples

## Overview

This directory contains two complete Node.js Executa plugin examples:

| Example | File | Description |
|---------|------|-------------|
| **Basic Plugin** | `example_plugin.js` | JSON formatting, Base64 encoding/decoding, hash computation |
| **Credential Plugin** | `credential_plugin.js` | GitHub query tool, demonstrating credential declaration and platform authorization integration |

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

In Anna Admin:
- Distribution method: **Local**
- Path: enter `node /path/to/example_plugin.js` (requires Node.js on the target machine)

## File Descriptions

| File | Description |
|------|-------------|
| `example_plugin.js` | Basic plugin main program |
| `credential_plugin.js` | Credential plugin example (platform authorization integration) |
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
