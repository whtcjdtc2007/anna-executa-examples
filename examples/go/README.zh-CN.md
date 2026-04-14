For English version, see [README.md](README.md)

# Go Executa 插件示例

## 概述

本目录包含三个独立的 Go Executa 插件示例。每个文件都是自包含的，可以独立构建/运行：

| 示例 | 文件 | 说明 |
|------|------|------|
| **基础插件** | `main.go` | 系统信息查询、哈希计算、字符串工具 |
| **凭据插件** | `credential_plugin.go` | Notion 查询工具，演示凭据声明与平台统一授权集成（API Key 模式） |
| **Google OAuth 插件** | `google_oauth_plugin.go` | Google Drive 文件浏览工具，演示通过平台授权使用 Google OAuth2 访问令牌 |

> **注意：** 每个文件都有自己的 `func main()`。使用 `go run <文件>.go` 运行特定插件，不要使用 `go run .`。

Go 天然编译为独立二进制，**零依赖**、跨平台，是 Binary 分发的理想选择。

## 运行方式

### 直接运行

```bash
# 基础插件（系统信息、哈希、字符串工具）
go run main.go

# 凭据插件（Notion API Key）
NOTION_TOKEN=ntn_xxx go run credential_plugin.go

# Google OAuth 插件（Drive 文件浏览）
GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go
```

测试协议：

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run main.go 2>/dev/null
```

### 构建二进制

```bash
# 当前平台（基础插件）
go build -o dist/example-go-tool main.go

# 凭据插件
go build -o dist/notion-credential-tool credential_plugin.go
go build -o dist/google-drive-tool google_oauth_plugin.go

# 或使用脚本 / Makefile
./build.sh
make build              # 仅基础插件
make build-all-plugins  # 全部三个插件
```

## 多平台构建

Go 的交叉编译是一等公民特性，无需安装额外工具：

### 使用 Makefile（推荐）

```bash
# 构建所有 7 个标准平台
make all

# 构建 + 打包
make package

# 构建 + 协议测试
make test
```

### 使用构建脚本

```bash
# 所有平台
./build.sh --all

# 构建 + 测试
./build.sh --all --test

# 构建 + 打包
./build.sh --package
```

### 手动交叉编译

```bash
# macOS Apple Silicon
GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-arm64 main.go

# macOS Intel
GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-darwin-x86_64 main.go

# Linux x86_64
GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-x86_64 main.go

# Linux ARM64
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o dist/example-go-tool-linux-aarch64 main.go

# Windows x86_64
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/example-go-tool-windows-x86_64.exe main.go
```

> `-ldflags="-s -w"` 去除调试信息，减小二进制体积约 30%。

## 分发到 Anna

Go 插件最适合 **Binary** 分发方式：

1. 构建所有平台：`make package`
2. 上传到 GitHub Releases（`dist/packages/*.tar.gz` / `*.zip`）
3. 在 Anna Admin 中配置每个平台的 Binary URL：

```
darwin-arm64    →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-darwin-arm64.tar.gz
linux-x86_64    →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-linux-x86_64.tar.gz
windows-x86_64  →  https://github.com/you/repo/releases/download/v1.0/example-go-tool-windows-x86_64.zip
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.go` | 基础插件主程序（完整实现） |
| `credential_plugin.go` | 凭据插件示例 — API Key 模式（演示平台统一授权集成） |
| `google_oauth_plugin.go` | Google OAuth 插件示例 — Drive 文件浏览（通过平台 OAuth 令牌） |
| `go.mod` | Go 模块定义 |
| `Makefile` | 多平台构建、测试、打包 |
| `build.sh` | 一键构建脚本 |

## 凭据插件示例

`credential_plugin.go` 演示如何与 Anna Nexus 的平台统一授权集成：

```go
// Manifest 中声明凭据（命名与平台提供商对齐）
"credentials": []map[string]any{
    {
        "name":      "NOTION_TOKEN",  // 与平台 credential_mapping 对齐
        "sensitive":  true,
    },
},

// 三层优先级读取凭据
func getCredential(credentials map[string]any, name string, defaultValue string) string {
    // 1. context.credentials（平台注入）
    // 2. os.Getenv（本地开发）
    // 3. defaultValue
}
```

本地开发测试：

```bash
# 通过环境变量提供凭据
NOTION_TOKEN=ntn_xxx go run credential_plugin.go

# 测试带凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"search_pages","arguments":{"query":"test"},"context":{"credentials":{"NOTION_TOKEN":"ntn_test"}}},"id":2}' | go run credential_plugin.go 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md)

## Google OAuth 插件示例

`google_oauth_plugin.go` 演示如何使用平台提供的 **OAuth2 访问令牌**来浏览 Google Drive 文件。插件不管理 OAuth 流程 — Nexus 处理授权、令牌交换和自动刷新。

### 与 API Key 插件的关键区别

从插件开发角度看，代码**完全一致** — 都是从 `context.credentials` 读取：

```go
// API Key 插件
"credentials": []map[string]any{{"name": "NOTION_TOKEN", ...}}

// OAuth 插件 — 平台自动注入
"credentials": []map[string]any{{"name": "GOOGLE_ACCESS_TOKEN", ...}}  // 映射到 $access_token
```

### 本地开发测试

```bash
# 通过环境变量提供 OAuth 令牌
GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go

# 测试 describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | go run google_oauth_plugin.go 2>/dev/null

# 测试带 OAuth 凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_files","arguments":{"max_results":5},"context":{"credentials":{"GOOGLE_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | go run google_oauth_plugin.go 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md) 了解完整 OAuth 流程

## 协议交互示例

```bash
BINARY=./dist/example-go-tool

# 获取工具清单
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | $BINARY 2>/dev/null

# 查询系统信息
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"system_info","arguments":{}},"id":2}' | $BINARY 2>/dev/null

# 计算哈希
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"hash_text","arguments":{"text":"hello","algorithm":"sha256"}},"id":3}' | $BINARY 2>/dev/null

# 字符串操作
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"string_utils","arguments":{"text":"hello","operation":"reverse"}},"id":4}' | $BINARY 2>/dev/null
```

## 添加自己的工具

1. 在 `manifest` 的 `tools` 数组中添加工具定义
2. 实现工具函数（接收 `map[string]any` 参数，返回 `map[string]any` 结果）
3. 在 `handleInvoke` 的 switch 中注册

```go
// 1. manifest 中添加定义
{
    "name":        "my_tool",
    "description": "...",
    "parameters": []map[string]any{...},
}

// 2. 实现
func toolMyTool(args map[string]any) map[string]any {
    input, _ := args["input"].(string)
    return map[string]any{"result": input}
}

// 3. 注册（在 handleInvoke 的 switch 中）
case "my_tool":
    result = toolMyTool(args)
```

## Go 的优势

| 特性 | 说明 |
|------|------|
| 零依赖 | 编译后无需安装任何运行时 |
| 极小体积 | 典型二进制 5-15 MB（对比 Python 50-200 MB） |
| 交叉编译 | 内置支持，一条命令即可 |
| 启动快 | 毫秒级冷启动 |
| 并发 | goroutine 天然支持并行计算 |
