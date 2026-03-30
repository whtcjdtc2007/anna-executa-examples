#!/usr/bin/env node
/**
 * Executa 插件示例 — Node.js 实现
 *
 * 这是一个完整的 Node.js Executa 插件示例，实现了 JSON 格式化和 Base64 编解码工具。
 *
 * 运行方式：
 *   node example_plugin.js
 *
 * 安装为 npm 全局工具：
 *   npm install -g .
 *
 * 协议要求：
 *   - stdin:  接收 JSON-RPC 请求（每行一个 JSON 对象）
 *   - stdout: 返回 JSON-RPC 响应（每行一个 JSON 对象）
 *   - stderr: 日志输出（不会干扰协议通信）
 */

const readline = require("readline");

// ─── Manifest（自描述清单） ──────────────────────────────────────────
//
// name:         工具唯一标识符，对应 Anna Admin 的 tool_id
// display_name: 人类可读名称，对应 Anna Admin 的 name

const MANIFEST = {
  name: "example-node-tool",
  display_name: "Example Node.js Tool",
  version: "1.0.0",
  description: "一个 Node.js 示例工具，演示 Executa 插件协议",
  author: "Anna Developer",
  tools: [
    {
      name: "json_format",
      description: "格式化 JSON 字符串，支持自定义缩进",
      parameters: [
        {
          name: "json_string",
          type: "string",
          description: "要格式化的 JSON 字符串",
          required: true,
        },
        {
          name: "indent",
          type: "integer",
          description: "缩进空格数（默认 2）",
          required: false,
          default: 2,
        },
      ],
    },
    {
      name: "base64_encode",
      description: "Base64 编码文本",
      parameters: [
        {
          name: "text",
          type: "string",
          description: "要编码的文本",
          required: true,
        },
      ],
    },
    {
      name: "base64_decode",
      description: "Base64 解码文本",
      parameters: [
        {
          name: "encoded",
          type: "string",
          description: "要解码的 Base64 字符串",
          required: true,
        },
      ],
    },
    {
      name: "hash_text",
      description: "计算文本的 SHA-256 / MD5 哈希值",
      parameters: [
        {
          name: "text",
          type: "string",
          description: "要哈希的文本",
          required: true,
        },
        {
          name: "algorithm",
          type: "string",
          description: "哈希算法: sha256 / md5（默认 sha256）",
          required: false,
          default: "sha256",
        },
      ],
    },
  ],
  runtime: {
    type: "npm",
    min_version: "1.0.0",
  },
};

// ─── 工具实现 ─────────────────────────────────────────────────────

const crypto = require("crypto");

function toolJsonFormat(args) {
  const { json_string, indent = 2 } = args;
  try {
    const parsed = JSON.parse(json_string);
    return { formatted: JSON.stringify(parsed, null, indent), valid: true };
  } catch (e) {
    return { error: `Invalid JSON: ${e.message}`, valid: false };
  }
}

function toolBase64Encode(args) {
  const { text } = args;
  const encoded = Buffer.from(text, "utf-8").toString("base64");
  return { encoded, original_length: text.length };
}

function toolBase64Decode(args) {
  const { encoded } = args;
  try {
    const decoded = Buffer.from(encoded, "base64").toString("utf-8");
    return { decoded, encoded_length: encoded.length };
  } catch (e) {
    return { error: `Decode failed: ${e.message}` };
  }
}

function toolHashText(args) {
  const { text, algorithm = "sha256" } = args;
  const supported = ["sha256", "md5", "sha1", "sha512"];
  if (!supported.includes(algorithm)) {
    return {
      error: `Unsupported algorithm: ${algorithm}. Available: ${supported.join(", ")}`,
    };
  }
  const hash = crypto.createHash(algorithm).update(text, "utf-8").digest("hex");
  return { hash, algorithm, input_length: text.length };
}

const TOOL_DISPATCH = {
  json_format: toolJsonFormat,
  base64_encode: toolBase64Encode,
  base64_decode: toolBase64Decode,
  hash_text: toolHashText,
};

// ─── JSON-RPC 处理 ───────────────────────────────────────────────

function makeResponse(id, result, error) {
  const resp = { jsonrpc: "2.0", id };
  if (error !== undefined) resp.error = error;
  else resp.result = result;
  return resp;
}

function handleRequest(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    return makeResponse(null, undefined, {
      code: -32700,
      message: "Parse error",
    });
  }

  const { id, method, params = {} } = request;

  switch (method) {
    case "describe":
      return makeResponse(id, MANIFEST);

    case "invoke": {
      const { tool, arguments: args = {} } = params;
      if (!tool) {
        return makeResponse(id, undefined, {
          code: -32602,
          message: "Missing 'tool' in params",
        });
      }
      const fn = TOOL_DISPATCH[tool];
      if (!fn) {
        return makeResponse(id, undefined, {
          code: -32601,
          message: `Unknown tool: ${tool}`,
          data: { available_tools: Object.keys(TOOL_DISPATCH) },
        });
      }
      try {
        const result = fn(args);
        return makeResponse(id, { success: true, data: result, tool });
      } catch (e) {
        return makeResponse(id, undefined, {
          code: -32603,
          message: `Tool execution failed: ${e.message}`,
        });
      }
    }

    case "health":
      return makeResponse(id, {
        status: "healthy",
        timestamp: new Date().toISOString(),
        version: MANIFEST.version,
        tools_count: MANIFEST.tools.length,
      });

    default:
      return makeResponse(id, undefined, {
        code: -32601,
        message: `Method not found: ${method}`,
      });
  }
}

// ─── 主循环（stdio JSON-RPC 服务） ──────────────────────────────

const rl = readline.createInterface({ input: process.stdin });

process.stderr.write("🔌 Example Node.js Executa plugin started\n");
process.stderr.write(
  `   Tools: ${Object.keys(TOOL_DISPATCH).join(", ")}\n`
);

rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;

  process.stderr.write(`← ${line}\n`);
  const response = JSON.stringify(handleRequest(line));
  process.stdout.write(response + "\n");
  process.stderr.write(`→ ${response}\n`);
});
