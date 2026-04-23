For English version, see [README.md](README.md)

# Node.js Executa 插件示例

## 概述

本目录包含三个完整的 Node.js Executa 插件示例：

| 示例 | 文件 | 说明 |
|------|------|------|
| **基础插件** | `example_plugin.js` | JSON 格式化、Base64 编解码、哈希计算 |
| **凭据插件** | `credential_plugin.js` | GitHub 查询工具，演示凭据声明与平台统一授权集成（API Key 模式） |
| **Google OAuth 插件** | `google_oauth_plugin.js` | Google Calendar 日程管理工具，演示通过平台授权使用 Google OAuth2 访问令牌 |

## 运行方式

### 直接运行（开发/调试）

```bash
node example_plugin.js
```

在另一个终端测试：

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null
```

### 通过 npm 全局安装

```bash
npm install -g .
example-node-tool
```

### 通过 npm 脚本

```bash
npm start         # 运行插件
npm test          # 测试 describe
npm run build     # 构建二进制
npm run build:test  # 构建并测试
```

## 构建为独立二进制

### pkg（推荐，简单通用）

```bash
# 构建当前平台
./build_binary.sh

# 构建所有平台
./build_binary.sh --all

# 构建并测试
./build_binary.sh --test
```

### Node.js SEA（Node.js 20+，官方方案）

```bash
./build_binary.sh --sea --test
```

> **注意：** SEA 仅支持构建当前平台的二进制。交叉编译请使用 pkg。

### 手动使用 pkg

```bash
npx pkg example_plugin.js --targets node18-macos-arm64,node18-linux-x64 --output dist/example-node-tool
```

## 分发到 Anna

### npm 分发

在 Anna Admin 中：
- 分发方式：**npm**
- 包名：`example-node-tool`

### Binary 分发

1. 构建多平台二进制：`./build_binary.sh --all`
2. 打包：
   ```bash
   cd dist
   tar czf example-node-tool-darwin-arm64.tar.gz example-node-tool-darwin-arm64
   tar czf example-node-tool-linux-x86_64.tar.gz example-node-tool-linux-x86_64
   ```
3. 上传到 GitHub Releases / S3
4. 在 Anna Admin 中配置每个平台的 Binary URL

### Local 分发

Local 与 Binary 完全走同一套安装流程（解压 → `tools/{tool_id}/v{version}/` → 切 `current` 软链），只是 archive 从 Agent 主机本地路径读取、不需 HTTP 服务器。

推荐先用 `pkg` / SEA 打出独立二进制再打包：

```bash
./build_binary.sh
cd dist && tar czf example-node-tool.tar.gz example-node-tool-*
```

然后在 Anna Admin：
- 分发方式：**Local**
- Local Archive Path：`/abs/path/to/dist/example-node-tool.tar.gz`
- Version：`dev`（或任意字符串）

## 文件说明

| 文件 | 说明 |
|------|------|
| `example_plugin.js` | 基础插件主程序 |
| `credential_plugin.js` | 凭据插件示例 — API Key 模式（演示平台统一授权集成） |
| `google_oauth_plugin.js` | Google OAuth 插件示例 — Calendar 日程管理（通过平台 OAuth 令牌） |
| `package.json` | npm 包配置 |
| `build_binary.sh` | 一键构建脚本（pkg / SEA） |

## 凭据插件示例

`credential_plugin.js` 演示如何与 Anna Nexus 的平台统一授权集成：

```javascript
// Manifest 中声明凭据（命名与平台提供商对齐）
credentials: [
  {
    name: "GITHUB_TOKEN",           // 与平台 credential_mapping 对齐
    display_name: "Personal Access Token",
    required: true,
    sensitive: true,
  },
]

// 工具函数中读取凭据（三层优先级）
function toolGetRepo(args, credentials) {
  // 1. 平台统一凭据 / 插件级凭据（Agent 注入）
  // 2. 环境变量回退（本地开发）
  const token = getCredential(credentials, "GITHUB_TOKEN");
}
```

本地开发测试：

```bash
# 通过环境变量提供凭据
GITHUB_TOKEN=ghp_xxx node credential_plugin.js

# 测试带凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_repo","arguments":{"owner":"octocat","repo":"hello-world"},"context":{"credentials":{"GITHUB_TOKEN":"ghp_test"}}},"id":2}' | node credential_plugin.js 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md)

## Google OAuth 插件示例

`google_oauth_plugin.js` 演示如何使用平台提供的 **OAuth2 访问令牌**来管理 Google Calendar 日程。插件不管理 OAuth 流程 — Nexus 处理授权、令牌交换和自动刷新。

### 与 API Key 插件的关键区别

从插件开发角度看，代码**完全一致** — 都是从 `context.credentials` 读取：

```javascript
// API Key 插件
credentials: [{name: "GITHUB_TOKEN", ...}]

// OAuth 插件 — 平台自动注入
credentials: [{name: "GOOGLE_ACCESS_TOKEN", ...}]  // 映射到 $access_token
```

### 本地开发测试

```bash
# 通过环境变量提供 OAuth 令牌
GOOGLE_ACCESS_TOKEN=ya29.xxx node google_oauth_plugin.js

# 测试 describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node google_oauth_plugin.js 2>/dev/null

# 测试带 OAuth 凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_events","arguments":{"max_results":5},"context":{"credentials":{"GOOGLE_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | node google_oauth_plugin.js 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md) 了解完整 OAuth 流程

## 协议交互示例

```bash
# 获取工具清单
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | node example_plugin.js 2>/dev/null

# 调用 json_format
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"json_format","arguments":{"json_string":"{\"a\":1}","indent":4}},"id":2}' | node example_plugin.js 2>/dev/null

# 调用 base64_encode
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"base64_encode","arguments":{"text":"hello world"}},"id":3}' | node example_plugin.js 2>/dev/null

# 调用 hash_text
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"hash_text","arguments":{"text":"hello","algorithm":"sha256"}},"id":4}' | node example_plugin.js 2>/dev/null
```

## 添加自己的工具

1. 在 `MANIFEST.tools` 中添加工具定义
2. 实现工具函数（接收 `args` 对象，返回 result 对象）
3. 在 `TOOL_DISPATCH` 中注册

```javascript
// 1. 定义
{ name: "my_tool", description: "...", parameters: [...] }

// 2. 实现
function toolMyTool(args) {
  const { input } = args;
  return { result: "..." };
}

// 3. 注册
TOOL_DISPATCH["my_tool"] = toolMyTool;
```
