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
const fs = require("fs");
const os = require("os");
const path = require("path");

// 单条 stdio 消息大小阈值（字节），超过后自动使用文件传输
const MAX_STDIO_MESSAGE_BYTES = 512 * 1024;

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
    {
      name: "batch_hash",
      description: "批量计算多段文本的哈希值（演示 array 参数用法）",
      parameters: [
        {
          name: "texts",
          type: "array",
          items: { type: "string" },
          description: "要计算哈希的文本列表",
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
    {
      name: "generate_dataset",
      description: "生成模拟数据集（可产生大型响应，演示文件传输机制）",
      parameters: [
        {
          name: "rows",
          type: "integer",
          description: "生成的数据行数（1-100000，超过约 5000 行时会触发文件传输）",
          required: false,
          default: 100,
        },
        {
          name: "columns",
          type: "array",
          items: { type: "string" },
          description: "要包含的列名列表，可选: id / name / email / score / timestamp / description",
          required: false,
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

function toolBatchHash(args) {
  const { texts, algorithm = "sha256" } = args;
  const supported = ["sha256", "md5", "sha1", "sha512"];
  if (!supported.includes(algorithm)) {
    return {
      error: `Unsupported algorithm: ${algorithm}. Available: ${supported.join(", ")}`,
    };
  }
  const results = texts.map((text) => {
    const hash = crypto
      .createHash(algorithm)
      .update(text, "utf-8")
      .digest("hex");
    return { text_preview: text.slice(0, 50), hash, algorithm };
  });
  return { count: results.length, results };
}

const FIRST_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
  "Grace", "Hank", "Ivy", "Jack", "Karen", "Leo"];
const LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones",
  "Garcia", "Miller", "Davis", "Wilson", "Taylor"];
const LOREM_WORDS = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
  "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
  "incididunt", "ut", "labore", "et", "dolore", "magna"];

/** 简单的可植种伪随机生成器（保证可复现） */
function seededRandom(seed) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 0) / 0xffffffff;
  };
}

function toolGenerateDataset(args) {
  let { rows = 100, columns } = args;
  rows = Math.max(1, Math.min(100000, rows));

  const availableCols = ["id", "name", "email", "score", "timestamp", "description"];
  if (!columns || !Array.isArray(columns) || columns.length === 0) {
    columns = ["id", "name", "email", "score"];
  }
  columns = columns.filter((c) => availableCols.includes(c));
  if (columns.length === 0) columns = ["id"];

  const rand = seededRandom(42);
  const pick = (arr) => arr[Math.floor(rand() * arr.length)];
  const randInt = (min, max) => min + Math.floor(rand() * (max - min + 1));

  const dataset = [];
  for (let i = 0; i < rows; i++) {
    const row = {};
    for (const col of columns) {
      switch (col) {
        case "id":
          row.id = i + 1;
          break;
        case "name":
          row.name = `${pick(FIRST_NAMES)} ${pick(LAST_NAMES)}`;
          break;
        case "email": {
          const n = `${pick(FIRST_NAMES)}.${pick(LAST_NAMES)}`.toLowerCase();
          row.email = `${n}@example.com`;
          break;
        }
        case "score":
          row.score = Math.round(rand() * 10000) / 100;
          break;
        case "timestamp": {
          const ts = 1700000000 + randInt(0, 10000000);
          row.timestamp = new Date(ts * 1000).toISOString();
          break;
        }
        case "description": {
          const words = [];
          const count = randInt(10, 30);
          for (let w = 0; w < count; w++) words.push(pick(LOREM_WORDS));
          row.description = words.join(" ");
          break;
        }
      }
    }
    dataset.push(row);
  }

  const sampleJson = JSON.stringify(dataset.slice(0, 1));
  const estimatedBytes = Buffer.byteLength(sampleJson, "utf-8") * rows;

  return {
    rows,
    columns,
    estimated_bytes: estimatedBytes,
    file_transport: estimatedBytes > MAX_STDIO_MESSAGE_BYTES,
    dataset,
  };
}

const TOOL_DISPATCH = {
  json_format: toolJsonFormat,
  base64_encode: toolBase64Encode,
  base64_decode: toolBase64Decode,
  hash_text: toolHashText,
  batch_hash: toolBatchHash,
  generate_dataset: toolGenerateDataset,
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

// ─── 响应发送（支持文件传输） ─────────────────────────────────────

/**
 * 发送 JSON-RPC 响应，大型结果自动使用文件传输。
 *
 * 当序列化后的 JSON 超过 MAX_STDIO_MESSAGE_BYTES 时，将完整响应
 * 写入临时文件，通过 stdout 只发送一条包含文件路径的轻量指针。
 * Agent 读取后会自动删除临时文件。
 */
function sendResponse(responseObj) {
  const payload = JSON.stringify(responseObj);
  const payloadBytes = Buffer.byteLength(payload, "utf-8");

  if (payloadBytes > MAX_STDIO_MESSAGE_BYTES) {
    // 生成唯一临时文件路径
    const tmpPath = path.join(
      os.tmpdir(),
      `executa-resp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`
    );
    fs.writeFileSync(tmpPath, payload, "utf-8");

    // 发送文件指针
    const pointer = JSON.stringify({
      jsonrpc: "2.0",
      id: responseObj.id,
      __file_transport: tmpPath,
    });
    process.stderr.write(
      `📦 Response too large (${payloadBytes} bytes), ` +
        `using file transport: ${tmpPath}\n`
    );
    process.stdout.write(pointer + "\n");
  } else {
    process.stdout.write(payload + "\n");
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
  const responseObj = handleRequest(line);
  sendResponse(responseObj);
  process.stderr.write(`→ (sent, ${JSON.stringify(responseObj).length} bytes)\n`);
});
