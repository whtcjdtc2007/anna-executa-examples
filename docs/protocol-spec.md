# Executa 协议规范

## 概述

Executa 使用 **JSON-RPC 2.0 over stdio** 进行通信。这是一种简单、高效且语言无关的 IPC 方式：

- Agent（父进程）通过 **stdin** 向插件发送 JSON-RPC 请求
- 插件通过 **stdout** 返回 JSON-RPC 响应
- 日志和调试信息输出到 **stderr**

每条消息占一行（行分隔的 JSON），不使用 Content-Length 头。

## 传输层

```
┌──────────┐   stdin (JSON-RPC request)    ┌──────────┐
│          │ ──────────────────────────────→│          │
│  Anna    │                                │  Plugin  │
│  Agent   │   stdout (JSON-RPC response)   │  Process │
│          │ ←──────────────────────────────│          │
│          │                                │          │
│          │   stderr (logs, debug)         │          │
│          │ ←─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│          │
└──────────┘                                └──────────┘
```

### 关键约束

1. **stdout 仅用于协议响应** — 任何非 JSON-RPC 的 stdout 输出会导致协议解析失败
2. **stderr 用于日志** — 自由使用，Agent 会捕获但不解析
3. **每行一条消息** — 以 `\n` 分隔，不可跨行
4. **UTF-8 编码** — 所有文本必须为 UTF-8

## RPC 方法

### `describe` — 获取工具清单

Agent 启动插件后首先调用此方法，获取插件提供的所有工具信息。

**请求：**

```json
{
  "jsonrpc": "2.0",
  "method": "describe",
  "id": 1
}
```

**响应（Manifest）：**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "name": "my-awesome-tool",
    "display_name": "My Awesome Tool",
    "version": "1.0.0",
    "description": "工具的简要描述",
    "author": "Your Name",
    "tools": [
      {
        "name": "do_something",
        "description": "执行某个操作（此描述会展示给 LLM）",
        "parameters": [
          {
            "name": "input_text",
            "type": "string",
            "description": "输入文本",
            "required": true
          },
          {
            "name": "count",
            "type": "integer",
            "description": "重复次数",
            "required": false,
            "default": 1
          }
        ]
      }
    ],
    "runtime": {
      "type": "binary",
      "min_version": "1.0.0"
    }
  }
}
```

### Manifest 字段说明

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 工具唯一标识符（对应 Anna Admin 的 `tool_id`） |
| `display_name` | string | ✅ | 人类可读名称（对应 Admin 的 `name`） |
| `version` | string | ✅ | 语义化版本号 |
| `description` | string | ✅ | 工具描述 |
| `author` | string | 可选 | 作者信息 |
| `tools` | array | ✅ | 工具列表 |
| `runtime` | object | 可选 | 运行时信息 |

### 工具参数类型

| 类型 | JSON 类型 | 说明 |
|------|----------|------|
| `string` | string | 字符串 |
| `integer` | number | 整数 |
| `number` | number | 浮点数 |
| `boolean` | boolean | 布尔值 |
| `array` | array | 数组 |
| `object` | object | 对象 |

### `invoke` — 执行工具

LLM 决定使用某个工具时，Agent 通过此方法调用。

**请求：**

```json
{
  "jsonrpc": "2.0",
  "method": "invoke",
  "params": {
    "tool": "do_something",
    "arguments": {
      "input_text": "hello world",
      "count": 3
    }
  },
  "id": 2
}
```

**成功响应：**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "success": true,
    "data": {
      "output": "hello world hello world hello world"
    },
    "tool": "do_something"
  }
}
```

**错误响应：**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32602,
    "message": "Missing required parameter: input_text"
  }
}
```

### `health` — 健康检查（可选）

Agent 定期调用，确认插件进程仍在正常运行。

**请求：**

```json
{
  "jsonrpc": "2.0",
  "method": "health",
  "id": 3
}
```

**响应：**

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "status": "healthy",
    "timestamp": "2026-03-30T12:00:00Z",
    "version": "1.0.0",
    "tools_count": 2
  }
}
```

## 错误码

遵循 JSON-RPC 2.0 标准错误码，并扩展自定义码：

| 码 | 含义 | 场景 |
|----|------|------|
| `-32700` | Parse error | 请求不是有效的 JSON |
| `-32600` | Invalid request | 缺少 `jsonrpc` 或 `method` 字段 |
| `-32601` | Method not found | 未知的 RPC 方法或工具名 |
| `-32602` | Invalid params | 参数缺失或类型错误 |
| `-32603` | Internal error | 工具执行时发生异常 |

## 超时

| 方法 | 默认超时 | 说明 |
|------|---------|------|
| `describe` | 5 秒 | 启动后首次调用 |
| `health` | 3 秒 | 周期性检查 |
| `invoke` | 60 秒 | 工具执行（可配置） |

## 生命周期

```
1. Agent 启动插件进程 (fork + exec)
2. Agent → stdin: describe
3. Plugin → stdout: manifest (工具列表)
4. Agent 注册工具到 LLM tool schema
5. 循环:
   a. LLM 决定调用工具
   b. Agent → stdin: invoke {tool, arguments}
   c. Plugin → stdout: result / error
6. Agent 可选: 定期发送 health 检查
7. Agent 终止: 关闭 stdin → 插件退出
```

## 实现检查清单

- [ ] stdin 逐行读取 JSON（处理空行）
- [ ] stdout 输出 JSON 后 flush（避免缓冲导致阻塞）
- [ ] 所有日志输出到 stderr
- [ ] `describe` 返回完整 manifest
- [ ] `invoke` 能正确处理参数并返回结果
- [ ] 未知 method 返回 `-32601` 错误
- [ ] 异常时返回 JSON-RPC error 而非崩溃
- [ ] 主循环不会因单条请求异常而退出
