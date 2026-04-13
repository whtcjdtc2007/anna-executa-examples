#!/usr/bin/env node
/**
 * Executa 凭据插件示例 — Node.js 实现
 *
 * 演示如何：
 * 1. 在 Manifest 中声明所需凭据（credentials 字段）
 * 2. 在 invoke 中从 context.credentials 读取凭据
 * 3. 回退到环境变量以支持本地开发
 * 4. 安全地使用凭据调用外部 API
 *
 * 凭据的三层解析优先级：
 *   1. 平台统一凭据 — 用户在 /settings/authorizations 一次性配置
 *   2. 插件级凭据   — 用户在单个插件设置中手动填写
 *   3. 环境变量     — 本地开发时从 process.env 读取（插件自行实现）
 *
 * Agent 会将解析后的凭据通过 invoke 请求的 params.context.credentials 注入，
 * LLM 不会看到凭据内容，也无法在对话中泄露。
 *
 * 运行方式：
 *   node credential_plugin.js
 *
 * 本地开发（通过环境变量提供凭据）：
 *   GITHUB_TOKEN=ghp_xxx node credential_plugin.js
 *
 * 协议要求：
 *   - stdin:  接收 JSON-RPC 请求（每行一个 JSON 对象）
 *   - stdout: 返回 JSON-RPC 响应（每行一个 JSON 对象）
 *   - stderr: 日志输出（不会干扰协议通信）
 */

const readline = require("readline");

// ─── Manifest（自描述清单） ──────────────────────────────────────────
//
// credentials: 声明本插件所需的凭据列表
//
// credentials[].name 命名最佳实践：
//   - 使用全大写蛇形命名（如 GITHUB_TOKEN）
//   - 与平台提供商的 credential_mapping 对齐，实现自动映射
//     例如：GITHUB_TOKEN、TWITTER_API_KEY、GOOGLE_ACCESS_TOKEN
//   - 自定义服务用 SERVICE_NAME + 字段类型 命名
//
// sensitive=true 的凭据会在 UI 中以密码框显示，不回显明文。

const MANIFEST = {
  name: "github-tool",
  display_name: "GitHub Tool",
  version: "1.0.0",
  description: "GitHub 仓库查询工具，演示凭据（API Token）的声明与使用",
  author: "Anna Developer",
  credentials: [
    {
      name: "GITHUB_TOKEN",
      display_name: "Personal Access Token",
      description:
        "GitHub Settings → Developer Settings → Personal access tokens (fine-grained recommended)",
      required: true,
      sensitive: true,
    },
  ],
  tools: [
    {
      name: "get_repo",
      description: "查询 GitHub 仓库信息",
      parameters: [
        {
          name: "owner",
          type: "string",
          description: "仓库所有者（用户名或组织名）",
          required: true,
        },
        {
          name: "repo",
          type: "string",
          description: "仓库名称",
          required: true,
        },
      ],
    },
    {
      name: "list_issues",
      description: "列出仓库的 Issues",
      parameters: [
        {
          name: "owner",
          type: "string",
          description: "仓库所有者",
          required: true,
        },
        {
          name: "repo",
          type: "string",
          description: "仓库名称",
          required: true,
        },
        {
          name: "state",
          type: "string",
          description: "Issue 状态: open / closed / all（默认 open）",
          required: false,
          default: "open",
        },
        {
          name: "limit",
          type: "integer",
          description: "返回数量上限（1-30，默认 10）",
          required: false,
          default: 10,
        },
      ],
    },
  ],
  runtime: {
    type: "npm",
    min_version: "1.0.0",
  },
};

// ─── 凭据读取辅助函数 ─────────────────────────────────────────────
//
// 最佳实践：优先从 context.credentials 读取，回退到环境变量

/**
 * 获取凭据值（按优先级解析）
 * @param {Object|null} credentials - invoke 注入的 context.credentials
 * @param {string} name - 凭据名称
 * @param {string} [defaultValue] - 默认值
 * @returns {string|undefined}
 */
function getCredential(credentials, name, defaultValue) {
  const creds = credentials || {};
  return creds[name] || process.env[name] || defaultValue;
}

// ─── 工具实现 ─────────────────────────────────────────────────────

function toolGetRepo(args, credentials) {
  const { owner, repo } = args;
  const token = getCredential(credentials, "GITHUB_TOKEN");

  if (!token) {
    return {
      error: "GITHUB_TOKEN not configured",
      hint: [
        "配置方式（任选其一）：",
        "  1. 平台统一授权: /settings/authorizations 页面配置",
        "  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置",
        "  3. 本地开发: GITHUB_TOKEN=ghp_xxx node credential_plugin.js",
      ].join("\n"),
    };
  }

  // ─── 实际调用示例（注释） ───
  // const https = require("https");
  // const options = {
  //   hostname: "api.github.com",
  //   path: `/repos/${owner}/${repo}`,
  //   headers: {
  //     "Authorization": `Bearer ${token}`,
  //     "User-Agent": "anna-executa-plugin",
  //     "Accept": "application/vnd.github+json",
  //   },
  // };
  // ────────────────────────────

  // 模拟数据（演示用）
  return {
    full_name: `${owner}/${repo}`,
    description: "A simulated repository for demonstration",
    stars: 42,
    forks: 7,
    language: "TypeScript",
    open_issues: 3,
    token_configured: true,
    token_preview:
      token.length > 8 ? `${token.slice(0, 4)}...${token.slice(-4)}` : "***",
    _note: "This is simulated data for demonstration purposes",
  };
}

function toolListIssues(args, credentials) {
  const { owner, repo, state = "open", limit = 10 } = args;
  const token = getCredential(credentials, "GITHUB_TOKEN");

  if (!token) {
    return {
      error: "GITHUB_TOKEN not configured",
      hint: [
        "配置方式（任选其一）：",
        "  1. 平台统一授权: /settings/authorizations 页面配置",
        "  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置",
        "  3. 本地开发: GITHUB_TOKEN=ghp_xxx node credential_plugin.js",
      ].join("\n"),
    };
  }

  const effectiveLimit = Math.max(1, Math.min(30, limit));

  // 模拟数据
  const issues = [];
  for (let i = 0; i < effectiveLimit; i++) {
    issues.push({
      number: i + 1,
      title: `Example issue #${i + 1}`,
      state: state === "all" ? (i % 2 === 0 ? "open" : "closed") : state,
      labels: i % 3 === 0 ? ["bug"] : ["enhancement"],
      created_at: new Date(Date.now() - i * 86400000).toISOString(),
    });
  }

  return {
    repository: `${owner}/${repo}`,
    state,
    count: issues.length,
    issues,
    token_configured: true,
    _note: "This is simulated data for demonstration purposes",
  };
}

const TOOL_DISPATCH = {
  get_repo: toolGetRepo,
  list_issues: toolListIssues,
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
      const { tool, arguments: args = {}, context = {} } = params;
      const credentials = context.credentials || null;

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
        // 将凭据作为第二个参数传入工具函数
        const result = fn(args, credentials);
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
        credentials_declared: MANIFEST.credentials.length,
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

process.stderr.write("🔌 GitHub credential plugin started (Node.js)\n");
process.stderr.write(
  `   Tools: ${Object.keys(TOOL_DISPATCH).join(", ")}\n`
);
process.stderr.write(
  `   Credentials required: ${MANIFEST.credentials.map((c) => c.name).join(", ")}\n`
);

rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;

  process.stderr.write(`← ${line}\n`);
  const responseObj = handleRequest(line);
  const payload = JSON.stringify(responseObj);
  process.stdout.write(payload + "\n");
  process.stderr.write(`→ ${payload}\n`);
});
