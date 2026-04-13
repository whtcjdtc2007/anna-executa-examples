> For English version, see [README.md](README.md)

# Anna Executa Plugin Examples

本仓库提供 Anna Executa 插件的**完整示例与开发文档**，覆盖 Python、Node.js、Go 三种语言，以及 Local / Binary 两种分发方式。

## 什么是 Executa？

Executa 是 Anna Agent 的插件扩展系统。开发者可以用**任意编程语言**编写工具，只要实现标准的 **JSON-RPC 2.0 over stdio** 协议，即可被 Anna 自动发现、加载并提供给 LLM 调用。

## 目录结构

```
anna-executa-examples/
├── docs/                       # 开发文档
│   ├── protocol-spec.md        # 协议规范
│   ├── authorization.md        # 平台统一授权指南
│   └── binary-distribution.md  # Binary 分发指南
├── examples/
│   ├── python/                 # Python 插件示例
│   │   ├── example_plugin.py       # 基础插件（文本处理）
│   │   ├── credential_plugin.py    # 凭据插件（天气 API）
│   │   ├── pyproject.toml
│   │   ├── build_binary.sh
│   │   └── README.md
│   ├── nodejs/                 # Node.js 插件示例
│   │   ├── example_plugin.js       # 基础插件（JSON/Base64/Hash）
│   │   ├── credential_plugin.js    # 凭据插件（GitHub API）
│   │   ├── package.json
│   │   ├── build_binary.sh
│   │   └── README.md
│   └── go/                     # Go 插件示例
│       ├── main.go                 # 基础插件（系统信息/Hash）
│       ├── credential_plugin.go    # 凭据插件（Notion API）
│       ├── go.mod
│       ├── build.sh
│       ├── Makefile
│       └── README.md
└── .github/
    └── workflows/
        └── build-release.yml   # 多平台 CI/CD 示例
```

## 快速开始

### Python 插件

```bash
cd examples/python

# 直接运行
python example_plugin.py

# 测试协议
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# 构建为独立二进制
./build_binary.sh --test
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

# 直接运行
go run .

# 测试协议
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run . 2>/dev/null

# 构建本机二进制
go build -o dist/example-go-tool .

# 构建全平台二进制
make all
```

### 凭据插件（使用平台统一授权）

每种语言均提供了凭据插件示例，演示如何声明和使用平台统一授权的凭据：

```bash
# Python — 天气查询（需要 WEATHER_API_KEY）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python examples/python/credential_plugin.py 2>/dev/null

# 本地开发时通过环境变量提供凭据
WEATHER_API_KEY=your_key python examples/python/credential_plugin.py

# Node.js — GitHub 查询（需要 GITHUB_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node examples/nodejs/credential_plugin.js 2>/dev/null

# Go — Notion 查询（需要 NOTION_TOKEN）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run examples/go/credential_plugin.go 2>/dev/null
```

## 分发方式

| 方式 | 安装途径 | 适用场景 |
|------|---------|---------|
| **uv** | `uv tool install <package>` | Python 工具（推荐） |
| **pipx** | `pipx install <package>` | Python 工具 |
| **npm** | `npm install -g <package>` | Node.js 工具 |
| **Homebrew** | `brew install <formula>` | macOS/Linux |
| **Binary** | HTTP 下载 | 预编译二进制（任意语言） |
| **Local** | 本地路径 | 开发调试 |

## 文档

- [协议规范](docs/protocol-spec.md) — JSON-RPC 2.0 over stdio 完整协议定义
- [平台统一授权](docs/authorization.md) — 凭据声明、自动注入与平台授权集成
- [Binary 分发指南](docs/binary-distribution.md) — 构建、签名、多平台部署

## 许可证

MIT
