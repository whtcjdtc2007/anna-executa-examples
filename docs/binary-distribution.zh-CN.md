For English version, see [binary-distribution.md](binary-distribution.md)

# Executa Binary 分发指南

本文档面向开发者，介绍如何将 Executa 插件构建为独立二进制文件，并通过 Anna 的 Binary 分发机制部署到 Agent。

## 概述

Binary 分发允许你将插件编译为**独立可执行文件**，无需目标机器安装 Python/Node.js 等运行时。Anna Agent 通过 HTTP 下载二进制、自动解压并加载为 Executa 插件。

**适用场景：**
- 插件包含敏感逻辑，不希望以源码分发
- 目标机器无法安装 Python/Node.js 运行时
- 需要最快的冷启动速度
- 使用 Go/Rust/C++ 等编译型语言编写的插件

## 构建二进制

各语言均有完整示例：

| 语言 | 构建工具 | 示例目录 | 构建命令 |
|------|---------|---------|---------|
| Python | PyInstaller / Nuitka | `examples/python/` | `./build_binary.sh` |
| Node.js | pkg / nexe / esbuild+sea | `examples/nodejs/` | `./build_binary.sh` |
| Go | `go build` | `examples/go/` | `make all` |

### Python → 二进制

#### PyInstaller（推荐，简单快速）

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

产物：`dist/my-tool`

#### Nuitka（更高性能，体积更小）

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

### Node.js → 二进制

#### Node.js SEA（Single Executable Application，Node.js 20+，推荐）

```bash
# 1. 生成 sea-config.json
# 2. node --experimental-sea-config sea-config.json
# 3. 复制 node 可执行文件
# 4. 注入 blob
```

详见 `examples/nodejs/build_binary.sh`。

#### pkg（兼容性好，Node.js 18+）

```bash
npx pkg example_plugin.js \
    --targets node18-linux-x64,node18-macos-arm64,node18-win-x64
```

### Go → 二进制

Go 天然编译为独立二进制：

```bash
# 本机
go build -o dist/my-tool .

# 交叉编译
GOOS=darwin  GOARCH=arm64 go build -o dist/my-tool-darwin-arm64 .
GOOS=linux   GOARCH=amd64 go build -o dist/my-tool-linux-x86_64 .
GOOS=windows GOARCH=amd64 go build -o dist/my-tool-windows-x86_64.exe .
```

## 多平台构建

Anna 支持为同一个插件配置多平台二进制，Agent 安装时**自动检测 OS 和 CPU 架构**选择下载链接。

### 标准平台 Key

| Platform Key | 说明 |
|---|---|
| `darwin-arm64` | macOS Apple Silicon (M1/M2/M3/M4) |
| `darwin-x86_64` | macOS Intel |
| `linux-x86_64` | Linux x86_64 / AMD64 |
| `linux-aarch64` | Linux ARM64 (树莓派 4/5、ARM 服务器) |
| `linux-armv7l` | Linux ARMv7 (旧版树莓派) |
| `windows-x86_64` | Windows x86_64 |
| `windows-arm64` | Windows ARM64 |

### 在 Anna Admin 中配置

在 Admin → Executa 管理页面，选择 **Binary** 分发方式后，可以在「多平台二进制下载 URL」区域为每个平台配置独立的下载链接：

```
darwin-arm64    →  https://github.com/you/repo/releases/download/v1.0/my-tool-darwin-arm64.tar.gz
darwin-x86_64   →  https://github.com/you/repo/releases/download/v1.0/my-tool-darwin-x86_64.tar.gz
linux-x86_64    →  https://github.com/you/repo/releases/download/v1.0/my-tool-linux-x86_64.tar.gz
```

若只有一个版本，直接在「包名」字段填入 URL 即可，无需配置多平台映射。

### 平台匹配策略

Agent 安装时的匹配优先级：

1. **精确匹配** — Agent 平台为 `darwin-arm64`，直接命中
2. **OS 前缀降级** — 未找到精确匹配，尝试同 OS 的任意架构
3. **通配符** — 匹配 `*`、`any` 或 `universal` key
4. **单条目直通** — 若只配置了一个 URL，直接使用

## 打包格式

| 格式 | 处理方式 |
|---|---|
| `.tar.gz` / `.tgz` | 自动解压，取第一个可执行文件 |
| `.zip` | 自动解压，取第一个可执行文件 |
| 裸二进制（无扩展名） | 直接下载并 `chmod +x` |

推荐：macOS/Linux 用 `.tar.gz`，Windows 用 `.zip`。

打包示例：

```bash
# macOS / Linux
cd dist/ && tar czf my-tool-darwin-arm64.tar.gz my-tool

# Windows
cd dist/ && zip my-tool-windows-x86_64.zip my-tool.exe
```

## macOS 代码签名

### 本地开发 / 内部分发

Anna Agent 会在下载后自动清除 macOS quarantine 属性，即使未签名也可正常执行。

```bash
# ad-hoc 签名（满足本地开发需求）
codesign --force --sign - dist/my-tool
```

### 公开分发

面向外部用户分发，建议使用 Apple Developer ID 签名 + 公证：

```bash
# 1. Developer ID 签名
codesign --force --options runtime \
    --sign "Developer ID Application: Your Name (TEAM_ID)" \
    dist/my-tool

# 2. 公证
xcrun notarytool submit dist/my-tool.zip \
    --apple-id "your@email.com" \
    --team-id "TEAM_ID" \
    --password "app-specific-password" \
    --wait
```

## 验证二进制

```bash
# describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | ./dist/my-tool 2>/dev/null

# invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"your_tool","arguments":{"key":"value"}},"id":2}' | ./dist/my-tool 2>/dev/null

# health
echo '{"jsonrpc":"2.0","method":"health","id":3}' | ./dist/my-tool 2>/dev/null
```

## Agent 安装流程

```
1. 接收 RPC: install_plugin(package_name, distribution_type="binary", binary_urls={...})
2. 检测当前平台: get_platform_key() → "darwin-arm64"
3. 从 binary_urls 选择 URL: resolve_binary_url(binary_urls, "darwin-arm64")
4. curl 下载到临时目录
5. 根据文件类型解压（tar.gz / zip / 裸文件）
6. 移动到 ~/.anna/executa/bin/{name}
7. chmod 755
8. macOS: xattr -d com.apple.quarantine（清除 Gatekeeper 标记）
9. 自动执行 describe 获取 manifest
10. 注册为 Executa 插件，LLM 可调用
```

## CI/CD 自动化

完整的 GitHub Actions 多平台构建示例见 `.github/workflows/build-release.yml`。

## 常见问题

### Q: 二进制在 macOS 上无法执行？

Anna Agent 已自动清除 quarantine 属性。如果仍有问题：

```bash
xattr -d com.apple.quarantine ~/.anna/executa/bin/my-tool
codesign -vvv ~/.anna/executa/bin/my-tool
```

### Q: 如何支持只有一个通用二进制的情况？

在 Admin 的「包名」字段填入 URL，或在 `binary_urls` 中使用通配符 key：

```json
{ "*": "https://example.com/my-tool-universal.tar.gz" }
```

### Q: Python 插件二进制太大怎么办？

- 使用 **Nuitka** 替代 PyInstaller（体积更小）
- 使用 `--exclude-module` 排除不需要的模块
- 考虑改用 Go/Rust 重写核心逻辑

### Q: 是否支持自动更新？

当前不支持。重新执行安装操作会覆盖旧版本。
