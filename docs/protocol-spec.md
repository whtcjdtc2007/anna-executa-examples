中文版本请参阅 [protocol-spec.zh-CN.md](protocol-spec.zh-CN.md)

# Executa Protocol Specification

## Overview

Executa uses **JSON-RPC 2.0 over stdio** for communication. This is a simple, efficient, and language-agnostic IPC approach:

- The Agent (parent process) sends JSON-RPC requests to plugins via **stdin**
- Plugins return JSON-RPC responses via **stdout**
- Logs and debug information are output to **stderr**

Each message occupies one line (line-delimited JSON), without Content-Length headers.

## Transport Layer

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

### Key Constraints

1. **stdout is only for protocol responses** — Any non-JSON-RPC stdout output will cause protocol parsing failures
2. **stderr is for logging** — Use freely; the Agent captures but does not parse it
3. **One message per line** — Delimited by `\n`, must not span multiple lines
4. **UTF-8 encoding** — All text must be UTF-8

## RPC Methods

### `describe` — Get Tool Manifest

The Agent calls this method first after starting the plugin, to retrieve information about all tools the plugin provides.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "describe",
  "id": 1
}
```

**Response (Manifest):**

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

### Manifest Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Unique tool identifier (corresponds to `tool_id` in Anna Admin) |
| `display_name` | string | ✅ | Human-readable name (corresponds to `name` in Admin) |
| `version` | string | ✅ | Semantic version number |
| `description` | string | ✅ | Tool description |
| `author` | string | Optional | Author information |
| `tools` | array | ✅ | List of tools |
| `credentials` | array | Optional | Credential declaration list (see below) |
| `runtime` | object | Optional | Runtime information |

### Tool Parameter Types

| Type | JSON Type | Description |
|------|-----------|-------------|
| `string` | string | String |
| `integer` | number | Integer |
| `number` | number | Floating-point number |
| `boolean` | boolean | Boolean |
| `array` | array | Array (**must** provide `items` to declare element type) |
| `object` | object | Object |

### Credential Declaration `credentials`

Plugins that require API Keys / Tokens or other credentials should declare the required credentials in the Manifest via the `credentials` field.
The Agent platform will use this to render a configuration form in the UI and automatically inject credentials via `invoke`'s `context.credentials` when calling tools.

**Example (Twitter Plugin):**

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

**Credential Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Credential identifier (recommended: uppercase snake_case, e.g. `TWITTER_API_KEY`) |
| `display_name` | string | ✅ | Display name shown in UI |
| `description` | string | Optional | Instructions on how to obtain / help text |
| `required` | bool | Optional | Whether required (default: `true`) |
| `sensitive` | bool | Optional | Whether sensitive (UI renders as password field, default: `true`) |
| `default` | string | Optional | Default value |

**Design Principles:**

- Credentials **do not** appear in the tool parameter schema; the LLM cannot see or leak credential values
- The plugin receives credentials via `context.credentials` in the `invoke` request (see below)
- During local development, plugins can fall back to reading environment variables (`os.environ`) for convenience

### Array Parameter `items` Field

When a parameter type is `array`, you **must** declare the array element type via the `items` field.
This is critical to ensure the LLM correctly passes array parameters (rather than serializing them as quoted strings).

Two equivalent declaration methods are supported:

**Method 1: `items` object (recommended, compatible with standard JSON Schema)**

```json
{
  "name": "tags",
  "type": "array",
  "items": { "type": "string" },
  "description": "标签列表",
  "required": false
}
```

**Method 2: `items_type` string (shorthand)**

```json
{
  "name": "tags",
  "type": "array",
  "items_type": "string",
  "description": "标签列表",
  "required": false
}
```

If neither `items` nor `items_type` is provided, the Agent will default to treating it as a `string` array.

⚠️ **Important**: Missing `items` declarations are the main reason LLMs incorrectly wrap array parameters in quotes (e.g., passing `"['/path']"` instead of `["/path"]`).

### `invoke` — Execute Tool

When the LLM decides to use a tool, the Agent calls this method.

**Request:**

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

**Request with Credentials (when the plugin declares `credentials`):**

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

**`params` Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool` | string | ✅ | Tool name |
| `arguments` | object | ✅ | Tool parameters (populated by the LLM) |
| `context` | object | Optional | Runtime context (injected by the Agent platform, invisible to the LLM) |
| `context.credentials` | object | Optional | Credential key-value pairs (keys correspond to `credentials[].name` in the Manifest) |

> **Plugin Best Practice**: Prefer reading credentials from `context.credentials`, falling back to `os.environ` for local development compatibility.

**Success Response:**

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

**Error Response:**

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

### `health` — Health Check (Optional)

The Agent calls this periodically to confirm the plugin process is still running normally.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "health",
  "id": 3
}
```

**Response:**

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

## Error Codes

Follows JSON-RPC 2.0 standard error codes, with custom extensions:

| Code | Meaning | Scenario |
|------|---------|----------|
| `-32700` | Parse error | Request is not valid JSON |
| `-32600` | Invalid request | Missing `jsonrpc` or `method` field |
| `-32601` | Method not found | Unknown RPC method or tool name |
| `-32602` | Invalid params | Missing parameters or type mismatch |
| `-32603` | Internal error | Exception occurred during tool execution |

## Timeouts

| Method | Default Timeout | Description |
|--------|----------------|-------------|
| `describe` | 5 seconds | First call after startup |
| `health` | 3 seconds | Periodic check |
| `invoke` | 60 seconds | Tool execution (configurable) |

## Lifecycle

```
1. Agent starts the plugin process (fork + exec)
2. Agent → stdin: describe
3. Plugin → stdout: manifest (tool list)
4. Agent registers tools into the LLM tool schema
5. Loop:
   a. LLM decides to call a tool
   b. Agent → stdin: invoke {tool, arguments}
   c. Plugin → stdout: result / error
6. Agent optionally: sends periodic health checks
7. Agent terminates: closes stdin → plugin exits
```

## Implementation Checklist

- [ ] Read JSON from stdin line by line (handle empty lines)
- [ ] Flush stdout after outputting JSON (avoid blocking due to buffering)
- [ ] All logs go to stderr
- [ ] `describe` returns a complete manifest
- [ ] `describe`'s `credentials` declares all required credentials (if any)
- [ ] `invoke` correctly handles parameters and returns results
- [ ] `invoke` reads credentials from `params.context.credentials` (falls back to environment variables)
- [ ] Unknown methods return `-32601` error
- [ ] Return JSON-RPC error on exceptions instead of crashing
- [ ] Main loop does not exit due to a single request exception
- [ ] Large responses (>512KB) use file transport (see below)

## Large Responses — File Transport

When a tool's JSON response exceeds **512KB**, transmitting via the stdio pipe may cause buffer blocking or even process crashes. The protocol supports **file transport** as a safe channel for large messages.

### How It Works

The plugin writes the complete JSON-RPC response to a temporary file, then returns a lightweight pointer message via stdout containing the file path:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "__file_transport": "/tmp/executa-resp-xxxx.json"
}
```

After the Agent reads the `__file_transport` field:
1. Opens the file and reads the complete JSON-RPC response
2. Deletes the temporary file
3. Processes it as a normal response

### Plugin-Side Implementation Example (Python)

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

### Notes

- The temporary file must be readable by the Agent process (same user / same machine)
- The Agent automatically deletes the temporary file after reading
- The file must contain a complete JSON-RPC response (including `jsonrpc`, `id`, `result`/`error` fields)
- Even when not using file transport, plugins should ensure `flush` is called after every `write`

## stdout Buffering Considerations

Many languages use **block buffering** (not line buffering) for stdout by default, which can cause message delays or blocking in stdio IPC scenarios. Make sure to:

- **Python**: `sys.stdout.reconfigure(line_buffering=True)` or `flush()` after every `write`
- **Node.js**: `process.stdout` uses line buffering by default; usually no action needed
- **Go**: Use `bufio.Writer` and `Flush()` after every message
- **Rust**: Use `BufWriter` and `flush()` after every message
