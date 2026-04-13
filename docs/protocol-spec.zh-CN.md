For English version, see [protocol-spec.md](protocol-spec.md)

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
| `credentials` | array | 可选 | 凭据声明列表（见下文） |
| `runtime` | object | 可选 | 运行时信息 |

### 工具参数类型

| 类型 | JSON 类型 | 说明 |
|------|----------|------|
| `string` | string | 字符串 |
| `integer` | number | 整数 |
| `number` | number | 浮点数 |
| `boolean` | boolean | 布尔值 |
| `array` | array | 数组（**必须**提供 `items` 声明元素类型） |
| `object` | object | 对象 |

### 凭据声明 `credentials`

需要 API Key / Token 等凭据的插件，应在 Manifest 中通过 `credentials` 字段声明所需凭据。
Agent 平台会据此在 UI 中渲染配置表单，并在调用工具时通过 `invoke` 的 `context.credentials` 自动注入。

**示例（Twitter 插件）：**

```json
{
  "name": "twitter-tool",
  "display_name": "Twitter Tool",
  "version": "1.0.0",
  "credentials": [
    {
      "name": "TWITTER_API_KEY",
      "display_name": "API Key",
      "description": "Twitter Developer Portal 中获取的 API Key",
      "required": true
    },
    {
      "name": "TWITTER_API_SECRET",
      "display_name": "API Secret",
      "description": "Twitter Developer Portal 中获取的 API Secret",
      "required": true,
      "sensitive": true
    }
  ],
  "tools": [...]
}
```

**凭据字段说明：**

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 凭据标识符（建议全大写蛇形，如 `TWITTER_API_KEY`） |
| `display_name` | string | ✅ | UI 展示名称 |
| `description` | string | 可选 | 获取方式说明 / 帮助文本 |
| `required` | bool | 可选 | 是否必填（默认 `true`） |
| `sensitive` | bool | 可选 | 是否敏感（UI 渲染为密码框，默认 `true`） |
| `default` | string | 可选 | 默认值 |

**设计原则：**

- 凭据**不会**出现在工具参数 schema 中，LLM 无法看到或泄露凭据值
- 插件端通过 `invoke` 请求的 `context.credentials` 接收凭据（详见下文）
- 本地开发时，插件可回退读取环境变量（`os.environ`），保持本地调试便利性

### 数组参数的 `items` 字段

当参数类型为 `array` 时，**必须**通过 `items` 字段声明数组元素类型。
这是确保 LLM 正确传递数组参数（而非将其序列化为带引号的字符串）的关键。

支持两种等价的声明方式：

**方式 1：`items` 对象（推荐，兼容标准 JSON Schema）**

```json
{
  "name": "tags",
  "type": "array",
  "items": { "type": "string" },
  "description": "标签列表",
  "required": false
}
```

**方式 2：`items_type` 字符串（简写）**

```json
{
  "name": "tags",
  "type": "array",
  "items_type": "string",
  "description": "标签列表",
  "required": false
}
```

如果既不提供 `items` 也不提供 `items_type`，Agent 将默认按 `string` 数组处理。

⚠️ **重要**：缺少 `items` 声明是 LLM 将数组参数错误地用引号包裹（如传递 `"['/path']"` 而非 `["/path"]`）的主要原因。

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

**带凭据的请求（当插件声明了 `credentials` 时）：**

```json
{
  "jsonrpc": "2.0",
  "method": "invoke",
  "params": {
    "tool": "post_tweet",
    "arguments": {
      "text": "Hello World"
    },
    "context": {
      "credentials": {
        "TWITTER_API_KEY": "ak_xxxx",
        "TWITTER_API_SECRET": "sk_xxxx"
      }
    }
  },
  "id": 2
}
```

**`params` 字段说明：**

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `tool` | string | ✅ | 工具名称 |
| `arguments` | object | ✅ | 工具参数（由 LLM 填充） |
| `context` | object | 可选 | 运行时上下文（由 Agent 平台注入，LLM 不可见） |
| `context.credentials` | object | 可选 | 凭据键值对（key 对应 Manifest 中 `credentials[].name`） |

> **插件端最佳实践**：优先从 `context.credentials` 读取凭据，回退到 `os.environ` 以兼容本地开发。

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

## 凭据与平台统一授权

Executa 凭据系统与 Anna Nexus 的**平台统一授权**（Platform Authorization）深度集成。
用户在 Nexus 的 `/settings/authorizations` 页面一次性连接 Google、Twitter/X、GitHub 等服务后，
所有声明了对应凭据名称的插件自动获得凭据注入，无需逐个插件重复配置。

### 凭据解析优先级

```
1. 平台统一凭据（/settings/authorizations 配置的，最高优先级）
   ↓ 未找到
2. 插件级手动凭据（用户在单个插件设置中填写的）
   ↓ 未找到
3. 环境变量回退（仅本地开发场景，插件自行实现）
```

### 命名对齐

插件的 `credentials[].name` 应与平台提供商注册表中的命名对齐，实现自动映射：

| 平台 Provider | 推荐的凭据名称 | 说明 |
|--------------|--------------|------|
| Google (OAuth) | `GOOGLE_ACCESS_TOKEN` / `GMAIL_ACCESS_TOKEN` | 自动映射到 OAuth access_token |
| Twitter/X | `TWITTER_API_KEY` / `TWITTER_API_SECRET` / `TWITTER_BEARER_TOKEN` | 映射到用户填写的 API Key |
| GitHub | `GITHUB_TOKEN` | 映射到 Personal Access Token |
| Notion | `NOTION_TOKEN` | 映射到 Integration Token |
| Slack | `SLACK_BOT_TOKEN` | 映射到 Bot User OAuth Token |

### 插件端凭据读取最佳实践

```python
def my_tool(query: str, *, credentials: dict | None = None) -> dict:
    creds = credentials or {}
    # 1. 优先从 context.credentials 读取（平台注入）
    token = creds.get("GITHUB_TOKEN")
    # 2. 回退到环境变量（本地开发）
    if not token:
        token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {"error": "GITHUB_TOKEN not configured"}
    # 使用 token ...
```

> 详细的平台授权架构、API 端点和扩展方式，请参阅 [平台统一授权文档](authorization.zh-CN.md)。

## 实现检查清单

- [ ] stdin 逐行读取 JSON（处理空行）
- [ ] stdout 输出 JSON 后 flush（避免缓冲导致阻塞）
- [ ] 所有日志输出到 stderr
- [ ] `describe` 返回完整 manifest
- [ ] `describe` 的 `credentials` 声明所有需要的凭据（如有）
- [ ] `credentials[].name` 与平台提供商命名对齐（见上表）
- [ ] `invoke` 能正确处理参数并返回结果
- [ ] `invoke` 从 `params.context.credentials` 读取凭据（回退到环境变量）
- [ ] 凭据不出现在工具参数 schema 中（LLM 不可见）
- [ ] 敏感凭据标记 `sensitive: true`
- [ ] 未知 method 返回 `-32601` 错误
- [ ] 异常时返回 JSON-RPC error 而非崩溃
- [ ] 主循环不会因单条请求异常而退出
- [ ] 大型响应（>512KB）使用文件传输（见下文）

## 大型响应 — 文件传输（File Transport）

当工具返回的 JSON 响应超过 **512KB** 时，通过 stdio 管道传输可能导致缓冲区阻塞甚至进程崩溃。为此协议支持**文件传输**作为大型消息的安全通道。

### 工作原理

插件将完整的 JSON-RPC 响应写入临时文件，然后通过 stdout 返回一条包含文件路径的轻量指针消息：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "__file_transport": "/tmp/executa-resp-xxxx.json"
}
```

Agent 读到 `__file_transport` 字段后：
1. 打开该文件，读取完整的 JSON-RPC 响应
2. 删除临时文件
3. 按正常响应处理

### 插件侧实现示例（Python）

```python
import json, tempfile, sys

def send_response(response: dict) -> None:
    """发送响应，大型结果自动走文件传输"""
    payload = json.dumps(response, ensure_ascii=False)

    if len(payload.encode("utf-8")) > 512 * 1024:
        # 写入临时文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="executa-resp-",
            delete=False, encoding="utf-8"
        ) as f:
            f.write(payload)
            tmp_path = f.name
        # 发送文件指针
        pointer = json.dumps({
            "jsonrpc": "2.0",
            "id": response["id"],
            "__file_transport": tmp_path,
        })
        sys.stdout.write(pointer + "\n")
    else:
        sys.stdout.write(payload + "\n")

    sys.stdout.flush()
```

### 注意事项

- 临时文件必须对 Agent 进程可读（同一用户/同一机器）
- Agent 读取后会自动删除临时文件
- 文件必须包含完整的 JSON-RPC 响应（包括 `jsonrpc`、`id`、`result`/`error` 字段）
- 即使不使用文件传输，插件也应确保每次 `write` 后调用 `flush`

## stdout 缓冲注意事项

许多语言的 stdout 默认使用**块缓冲**（非行缓冲），这在 stdio IPC 场景中会导致消息延迟甚至阻塞。请确保：

- **Python**: `sys.stdout.reconfigure(line_buffering=True)` 或每次 `write` 后 `flush()`
- **Node.js**: `process.stdout` 默认行缓冲，通常无需处理
- **Go**: 使用 `bufio.Writer` 并在每条消息后 `Flush()`
- **Rust**: 使用 `BufWriter` 并在每条消息后 `flush()`
