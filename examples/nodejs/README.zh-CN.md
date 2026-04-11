For English version, see [README.md](README.md)

# Node.js Executa 插件示例

## 概述

这是一个完整的 Node.js Executa 插件示例，实现了 JSON 格式化、Base64 编解码和哈希计算工具。

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

在 Anna Admin 中：
- 分发方式：**Local**
- 路径填写 `node /path/to/example_plugin.js`（需要目标机器有 Node.js）

## 文件说明

| 文件 | 说明 |
|------|------|
| `example_plugin.js` | 插件主程序 |
| `package.json` | npm 包配置 |
| `build_binary.sh` | 一键构建脚本（pkg / SEA） |

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
