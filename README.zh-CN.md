> For English version, see [README.md](README.md)

# Anna Executa Plugin Examples

本仓库提供 Anna Executa 插件的**完整示例与开发文档**，覆盖 Python、Node.js、Go 三种语言，以及 Local / Binary 两种分发方式。

## 什么是 Executa？

Executa 是 Anna Agent 的插件扩展系统。开发者可以用**任意编程语言**编写工具，只要实现标准的 **JSON-RPC 2.0 over stdio** 协议，即可被 Anna 自动发现、加载并提供给 LLM 调用。

## 目录结构

```
anna-executa-examples/
├── docs/                                # 开发文档
│   ├── protocol-spec.md                 # 协议规范
│   ├── authorization.md                 # 平台统一授权指南
│   ├── binary-distribution.md           # Binary 分发指南
│   ├── sampling.md                      # 反向 LLM 采样（sampling/createMessage）
│   ├── persistent-storage.md            # Anna Persistent Storage（storage/* + files/*）
│   └── common-pitfalls.md               # ⚠️ 插件显示为 "Stopped" 时先看这里
├── examples/
│   ├── python/                          # Python 插件示例（每个示例独立子目录）
│   │   ├── basic-tool/                  # 基础插件（文本处理）
│   │   ├── credential-tool/             # 凭据插件（天气 API Key）
│   │   ├── google-oauth-tool/           # Google OAuth 插件（Gmail）
│   │   ├── sampling-summarizer/         # Sampling v2 插件（反向 sampling/createMessage）
│   │   ├── storage-notebook/            # Persistent Storage v2 插件（反向 storage/* + files/*）
│   │   └── build_binary.sh              # 通过 PyInstaller 构建所有示例（或单个）
│   ├── nodejs/                          # Node.js 插件示例
│   │   ├── example_plugin.js            # 基础插件（JSON/Base64/Hash）
│   │   ├── credential_plugin.js         # 凭据插件（GitHub API Key）
│   │   ├── google_oauth_plugin.js       # Google OAuth 插件（Calendar）
│   │   ├── sampling-tool.js             # Sampling v2 插件
│   │   └── build_binary.sh
│   ├── go/                              # Go 插件示例
│   │   ├── main.go                      # 基础插件（系统信息 / Hash）
│   │   ├── credential_plugin.go         # 凭据插件（Notion API Key）
│   │   ├── google_oauth_plugin.go       # Google OAuth 插件（Drive）
│   │   ├── sampling-tool/               # Sampling v2 插件（独立 go.mod）
│   │   ├── build.sh
│   │   └── Makefile
│   ├── multifile-binary/                # 多文件 Binary 分发示例
│   │   └── python-pyinstaller-onedir/   # PyInstaller --onedir + manifest.json
│   └── anna-app-focus-flow/             # ⭐ 完整 Anna App 示例（tool + skill + UI bundle + manifest）
├── sdk/                                 # Sampling 示例所使用的参考 SDK
│   ├── python/                          # executa_sdk
│   ├── nodejs/                          # @anna/executa-sdk
│   └── go/                              # github.com/anna/executa-sdk
└── .github/
    └── workflows/
        ├── build-release.yml            # 多平台 CI/CD 示例
        └── anna-app.yml                 # Anna App 打包工作流
```

## 快速开始

### Python 插件

每个 Python 示例都是独立子目录，自带 `pyproject.toml` 和 PyInstaller spec。

```bash
cd examples/python/basic-tool

# 直接运行
python example_plugin.py

# 测试协议
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# 通过 uv 安装
uv tool install . && example-text-tool

# 构建为独立二进制（在 examples/python/ 目录下，构建全部或单个子目录）
cd .. && ./build_binary.sh --test
```

### Node.js 插件

```bash
cd examples/nodejs

# 直接运行
node example_plugin.js

# 测试协议
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null

# 构建为独立二进制（需要 Node.js 18+）
./build_binary.sh --test
```

### Go 插件

```bash
cd examples/go

# 直接运行（每个插件都有自己的 func main，必须显式指定文件）
go run main.go

# 测试协议
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run main.go 2>/dev/null

# 构建本机二进制
go build -o dist/example-go-tool main.go

# 构建全平台 / 全插件二进制
make all
```

### 凭据插件 — API Key 模式

每种语言均提供了 API Key 凭据插件示例，演示如何声明和使用平台统一授权的凭据：

```bash
# Python — 天气查询（需要 WEATHER_API_KEY）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | \
  python examples/python/credential-tool/credential_plugin.py 2>/dev/null

# 本地开发时通过环境变量提供凭据
WEATHER_API_KEY=your_key python examples/python/credential-tool/credential_plugin.py

# Node.js — GitHub 查询（需要 GITHUB_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/credential_plugin.js 2>/dev/null

# Go — Notion 查询（需要 NOTION_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/credential_plugin.go 2>/dev/null
```

### 凭据插件 — OAuth2 令牌模式（Google）

Google OAuth 插件演示如何使用平台 OAuth2 授权注入的访问令牌。插件不处理 OAuth 流程 — Nexus 负责授权、令牌交换和自动刷新：

```bash
# Python — Gmail 邮件查询（GMAIL_ACCESS_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | \
  python examples/python/google-oauth-tool/google_oauth_plugin.py 2>/dev/null

# Node.js — Google Calendar 日程管理（GOOGLE_ACCESS_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/google_oauth_plugin.js 2>/dev/null

# Go — Google Drive 文件浏览（GOOGLE_ACCESS_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/google_oauth_plugin.go 2>/dev/null
```

> 从插件开发角度看，API Key 和 OAuth2 的代码完全一致 — 都是从 `context.credentials` 读取。区别仅在凭据名称映射和平台端配置。

### Sampling 插件（v2）— 反向 `sampling/createMessage`

插件可以请求宿主代为执行一次 LLM 补全。模型选择、计费与配额由宿主负责，插件无需任何 API Key。详见 [docs/sampling.zh-CN.md](docs/sampling.zh-CN.md)。

```bash
# Python
python examples/python/sampling-summarizer/sampling_summarizer.py

# Node.js
node examples/nodejs/sampling-tool.js

# Go（子目录使用独立的 go.mod）
cd examples/go/sampling-tool && go run ./...
```

### Persistent Storage 插件（v2）— 反向 `storage/*` + `files/*`

插件可以持久化按用户 / 应用维度的状态，并上传二进制附件，全程不需要任何
云存储凭据 —— bucket、加密、配额、按应用 ACL 均由 Anna 负责。详见
[docs/persistent-storage.zh-CN.md](docs/persistent-storage.zh-CN.md)。

```bash
# Python
python examples/python/storage-notebook/storage_notebook.py
```

## 分发方式

| 方式 | 安装途径 | 适用场景 |
|------|---------|---------|
| **uv** | `uv tool install <package>` | Python 工具（推荐） |
| **pipx** | `pipx install <package>` | Python 工具 |
| **npm** | `npm install -g <package>` | Node.js 工具 |
| **Homebrew** | `brew install <formula>` | macOS/Linux |
| **Binary** | HTTP 下载 | 预编译二进制（任意语言） |
| **Local** | Agent 主机上的本地压缩包（`.tar.gz`/`.tgz`/`.zip` 或 raw 可执行文件） | 开发迭代、内网/离线分发 — 与 Binary 同一套安装流程，支持多文件二进制（见 [`examples/multifile-binary/`](examples/multifile-binary/)） |

## 文档

- [协议规范](docs/protocol-spec.zh-CN.md) — JSON-RPC 2.0 over stdio 完整协议定义
- [平台统一授权](docs/authorization.zh-CN.md) — 凭据声明、自动注入与平台授权集成
- [Binary 分发指南](docs/binary-distribution.zh-CN.md) — 构建、签名、多平台部署
- [反向 Sampling](docs/sampling.zh-CN.md) — 插件请求宿主执行 LLM 补全
- [Persistent Storage](docs/persistent-storage.zh-CN.md) — 由 Anna 托管的按用户 / 应用维度 KV + 对象存储
- [常见踩坑](docs/common-pitfalls.md) — 插件显示为 "Stopped" 时先看这里
- [Anna App 示例 — Focus Flow](examples/anna-app-focus-flow/README.zh-CN.md) — 端到端 Anna App：1 工具 + 1 技能 + 高质感 UI bundle + 完整 manifest

## 许可证

MIT
