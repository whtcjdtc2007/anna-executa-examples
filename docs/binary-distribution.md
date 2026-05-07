中文版本请参阅 [binary-distribution.zh-CN.md](binary-distribution.zh-CN.md)

# Executa Binary Distribution Guide

This document is for developers, explaining how to build Executa plugins as standalone binary files and deploy them to an Agent via Anna's Binary distribution mechanism.

## Overview

Binary distribution allows you to compile plugins into **standalone executables**, without requiring the target machine to have Python/Node.js or other runtimes installed. The Anna Agent downloads binaries via HTTP, automatically extracts them, and loads them as Executa plugins.

**Applicable Scenarios:**
- Plugin contains sensitive logic and you prefer not to distribute source code
- Target machine cannot install Python/Node.js runtimes
- Fastest cold start speed is required
- Plugins written in compiled languages such as Go/Rust/C++

## Building Binaries

Complete examples are available for each language:

| Language | Build Tool | Example Directory | Build Command |
|----------|-----------|-------------------|---------------|
| Python | PyInstaller / Nuitka | `examples/python/<subdir>/` | `examples/python/build_binary.sh` (builds all) or `cd <subdir> && ../build_binary.sh` |
| Node.js | pkg / nexe / esbuild+sea | `examples/nodejs/` | `./build_binary.sh` |
| Go | `go build` | `examples/go/` | `make all` |

### Python → Binary

#### PyInstaller (Recommended, Simple and Fast)

```bash
pip install pyinstaller

pyinstaller \
    --onefile \
    --name my-tool \
    --clean \
    --strip \
    --noupx \
    my_plugin.py
```

Output: `dist/my-tool`

#### Nuitka (Higher Performance, Smaller Size)

```bash
pip install nuitka ordered-set

python3 -m nuitka \
    --standalone \
    --onefile \
    --output-filename=my-tool \
    --output-dir=dist \
    --remove-output \
    my_plugin.py
```

### Node.js → Binary

#### Node.js SEA (Single Executable Application, Node.js 20+, Recommended)

```bash
# 1. Generate sea-config.json
# 2. node --experimental-sea-config sea-config.json
# 3. Copy the node executable
# 4. Inject the blob
```

See `examples/nodejs/build_binary.sh` for details.

#### pkg (Good Compatibility, Node.js 18+)

```bash
npx pkg example_plugin.js \
    --targets node18-linux-x64,node18-macos-arm64,node18-win-x64
```

### Go → Binary

Go naturally compiles to standalone binaries:

```bash
# Native
go build -o dist/my-tool .

# Cross-compilation
GOOS=darwin  GOARCH=arm64 go build -o dist/my-tool-darwin-arm64 .
GOOS=linux   GOARCH=amd64 go build -o dist/my-tool-linux-x86_64 .
GOOS=windows GOARCH=amd64 go build -o dist/my-tool-windows-x86_64.exe .
```

## Multi-Platform Builds

Anna supports configuring multi-platform binaries for the same plugin. During installation, the Agent **automatically detects the OS and CPU architecture** to select the appropriate download link.

### Standard Platform Keys

| Platform Key | Description |
|---|---|
| `darwin-arm64` | macOS Apple Silicon (M1/M2/M3/M4) |
| `darwin-x86_64` | macOS Intel |
| `linux-x86_64` | Linux x86_64 / AMD64 |
| `linux-aarch64` | Linux ARM64 (Raspberry Pi 4/5, ARM servers) |
| `linux-armv7l` | Linux ARMv7 (older Raspberry Pi) |
| `windows-x86_64` | Windows x86_64 |
| `windows-arm64` | Windows ARM64 |

### Configuring in Anna Admin

In Admin → Executa management page, after selecting the **Binary** distribution method, you can configure independent download links for each platform in the "Multi-platform binary download URLs" section:

```
darwin-arm64    →  https://github.com/you/repo/releases/download/v1.0/my-tool-darwin-arm64.tar.gz
darwin-x86_64   →  https://github.com/you/repo/releases/download/v1.0/my-tool-darwin-x86_64.tar.gz
linux-x86_64    →  https://github.com/you/repo/releases/download/v1.0/my-tool-linux-x86_64.tar.gz
```

If there is only one version, simply enter the URL in the "Package name" field without configuring multi-platform mappings.

### Platform Matching Strategy

Priority order when the Agent installs:

1. **Exact match** — Agent platform is `darwin-arm64`, direct hit
2. **OS prefix fallback** — No exact match found, try any architecture with the same OS
3. **Wildcard** — Match `*`, `any`, or `universal` key
4. **Single entry passthrough** — If only one URL is configured, use it directly

## Package Formats

| Format | Handling |
|---|---|
| `.tar.gz` / `.tgz` | Automatically extracted, first executable file is used |
| `.zip` | Automatically extracted, first executable file is used |
| Raw binary (no extension) | Downloaded directly and `chmod +x` applied |

Recommended: Use `.tar.gz` for macOS/Linux, `.zip` for Windows.

Packaging examples:

```bash
# macOS / Linux
cd dist/ && tar czf my-tool-darwin-arm64.tar.gz my-tool

# Windows
cd dist/ && zip my-tool-windows-x86_64.zip my-tool.exe
```

## macOS Code Signing

### Local Development / Internal Distribution

The Anna Agent automatically clears the macOS quarantine attribute after download, so unsigned binaries can execute normally.

```bash
# ad-hoc signing (sufficient for local development)
codesign --force --sign - dist/my-tool
```

### Public Distribution

For distribution to external users, it is recommended to use Apple Developer ID signing + notarization:

```bash
# 1. Developer ID signing
codesign --force --options runtime \
    --sign "Developer ID Application: Your Name (TEAM_ID)" \
    dist/my-tool

# 2. Notarization
xcrun notarytool submit dist/my-tool.zip \
    --apple-id "your@email.com" \
    --team-id "TEAM_ID" \
    --password "app-specific-password" \
    --wait
```

## Verifying Binaries

```bash
# describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | ./dist/my-tool 2>/dev/null

# invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"your_tool","arguments":{"key":"value"}},"id":2}' | ./dist/my-tool 2>/dev/null

# health
echo '{"jsonrpc":"2.0","method":"health","id":3}' | ./dist/my-tool 2>/dev/null
```

## Agent Installation Flow

```
1. Receive RPC: install_plugin(package_name, distribution_type="binary", binary_urls={...})
2. Detect current platform: get_platform_key() → "darwin-arm64"
3. Select URL from binary_urls: resolve_binary_url(binary_urls, "darwin-arm64")
4. curl download to temporary directory
5. Extract based on file type (tar.gz / zip / raw file)
6. Move to ~/.anna/executa/bin/{name}
7. chmod 755
8. macOS: xattr -d com.apple.quarantine (clear Gatekeeper flag)
9. Automatically execute describe to get manifest
10. Register as Executa plugin, callable by LLM
```

## CI/CD Automation

See `.github/workflows/build-release.yml` for a complete GitHub Actions multi-platform build example.

## FAQ

### Q: Binary cannot execute on macOS?

The Anna Agent already automatically clears the quarantine attribute. If issues persist:

```bash
xattr -d com.apple.quarantine ~/.anna/executa/bin/my-tool
codesign -vvv ~/.anna/executa/bin/my-tool
```

### Q: How to support a single universal binary?

Enter the URL in the "Package name" field in Admin, or use a wildcard key in `binary_urls`:

```json
{ "*": "https://example.com/my-tool-universal.tar.gz" }
```

### Q: Python plugin binary is too large?

- Use **Nuitka** instead of PyInstaller (smaller size)
- Use `--exclude-module` to exclude unnecessary modules
- Consider rewriting core logic in Go/Rust

### Q: Is automatic updating supported?

Not currently. Re-executing the install operation will overwrite the old version.
