#!/usr/bin/env node
/**
 * Executa Plugin Example — Node.js Implementation
 *
 * A complete Node.js Executa plugin example implementing JSON formatting and Base64 encoding/decoding tools.
 *
 * Usage:
 *   node example_plugin.js
 *
 * Install as a global npm tool:
 *   npm install -g .
 *
 * Protocol requirements:
 *   - stdin:  Receives JSON-RPC requests (one JSON object per line)
 *   - stdout: Returns JSON-RPC responses (one JSON object per line)
 *   - stderr: Log output (does not interfere with protocol communication)
 *
 * ⚠️  CRITICAL — the plugin process must be LONG-RUNNING:
 *   - Listen on `rl.on("line", ...)` until stdin closes (the Agent closes stdin
 *     to shut you down). NEVER call `process.exit()` after one request.
 *   - `process.stdout.write(... + "\n")` is line-buffered; that's fine, but
 *     don't switch to `console.log` for protocol output (it doesn't guarantee
 *     a single JSON object per line).
 *   A one-shot process passes `describe` once and then shows up as **Stopped**
 *   in the Agent UI forever, paying a fresh cold-start on every invoke.
 */

const readline = require("readline");
const fs = require("fs");
const os = require("os");
const path = require("path");

// Single stdio message size threshold (bytes); file transport is used automatically when exceeded
const MAX_STDIO_MESSAGE_BYTES = 512 * 1024;

// ─── Manifest (Self-describing manifest) ────────────────────────────
//
// name:         Unique tool identifier, corresponds to tool_id in Anna Admin
// display_name: Human-readable name, corresponds to name in Anna Admin

const MANIFEST = {
  name: "example-node-tool",
  display_name: "Example Node.js Tool",
  version: "1.0.0",
  description: "A Node.js example tool demonstrating the Executa plugin protocol",
  author: "Anna Developer",
  tools: [
    {
      name: "json_format",
      description: "Format a JSON string with customizable indentation",
      parameters: [
        {
          name: "json_string",
          type: "string",
          description: "The JSON string to format",
          required: true,
        },
        {
          name: "indent",
          type: "integer",
          description: "Number of indentation spaces (default 2)",
          required: false,
          default: 2,
        },
      ],
    },
    {
      name: "base64_encode",
      description: "Base64 encode text",
      parameters: [
        {
          name: "text",
          type: "string",
          description: "The text to encode",
          required: true,
        },
      ],
    },
    {
      name: "base64_decode",
      description: "Base64 decode text",
      parameters: [
        {
          name: "encoded",
          type: "string",
          description: "The Base64 string to decode",
          required: true,
        },
      ],
    },
    {
      name: "hash_text",
      description: "Compute SHA-256 / MD5 hash of text",
      parameters: [
        {
          name: "text",
          type: "string",
          description: "The text to hash",
          required: true,
        },
        {
          name: "algorithm",
          type: "string",
          description: "Hash algorithm: sha256 / md5 (default sha256)",
          required: false,
          default: "sha256",
        },
      ],
    },
    {
      name: "batch_hash",
      description: "Batch compute hashes for multiple texts (demonstrates array parameter usage)",
      parameters: [
        {
          name: "texts",
          type: "array",
          items: { type: "string" },
          description: "List of texts to hash",
          required: true,
        },
        {
          name: "algorithm",
          type: "string",
          description: "Hash algorithm: sha256 / md5 (default sha256)",
          required: false,
          default: "sha256",
        },
      ],
    },
    {
      name: "generate_dataset",
      description: "Generate a simulated dataset (can produce large responses, demonstrating the file transport mechanism)",
      parameters: [
        {
          name: "rows",
          type: "integer",
          description: "Number of data rows to generate (1-100000; file transport is triggered above ~5000 rows)",
          required: false,
          default: 100,
        },
        {
          name: "columns",
          type: "array",
          items: { type: "string" },
          description: "List of column names to include; options: id / name / email / score / timestamp / description",
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

// ─── Tool implementations ────────────────────────────────────────────

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

/** Simple seeded pseudo-random generator (ensures reproducibility) */
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

// ─── JSON-RPC handling ───────────────────────────────────────────

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

// ─── Response sending (with file transport support) ──────────────

/**
 * Send a JSON-RPC response; large results automatically use file transport.
 *
 * When the serialized JSON exceeds MAX_STDIO_MESSAGE_BYTES, the full response
 * is written to a temporary file, and only a lightweight pointer containing
 * the file path is sent via stdout. The agent deletes the temp file after reading.
 */
function sendResponse(responseObj) {
  const payload = JSON.stringify(responseObj);
  const payloadBytes = Buffer.byteLength(payload, "utf-8");

  if (payloadBytes > MAX_STDIO_MESSAGE_BYTES) {
    // Generate a unique temporary file path
    const tmpPath = path.join(
      os.tmpdir(),
      `executa-resp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`
    );
    fs.writeFileSync(tmpPath, payload, "utf-8");

    // Send file pointer
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

// ─── Main loop (stdio JSON-RPC service) ─────────────────────────

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
