// Executa 凭据插件示例 — Go 实现
//
// 演示如何：
// 1. 在 Manifest 中声明所需凭据（credentials 字段）
// 2. 在 invoke 中从 context.credentials 读取凭据
// 3. 回退到环境变量以支持本地开发
// 4. 安全地使用凭据调用外部 API
//
// 凭据的三层解析优先级：
//  1. 平台统一凭据 — 用户在 /settings/authorizations 一次性配置
//  2. 插件级凭据   — 用户在单个插件设置中手动填写
//  3. 环境变量     — 本地开发时从 os.Getenv 读取（插件自行实现）
//
// 运行方式：
//
//	go run credential_plugin.go
//
// 本地开发（通过环境变量提供凭据）：
//
//	NOTION_TOKEN=ntn_xxx go run credential_plugin.go
//
// 构建：
//
//	go build -o dist/notion-tool credential_plugin.go
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strings"
	"time"
)

// ─── Manifest ──────────────────────────────────────────────────────
//
// credentials[].name 命名最佳实践：
//   - 使用全大写蛇形命名（如 NOTION_TOKEN）
//   - 与平台提供商的 credential_mapping 对齐，实现自动映射
//     例如：NOTION_TOKEN、GITHUB_TOKEN、GOOGLE_ACCESS_TOKEN
//   - 自定义服务用 SERVICE_NAME + 字段类型 命名
//
// sensitive=true 的凭据会在 UI 中以密码框显示，不回显明文。

var manifest = map[string]any{
	"name":         "notion-tool",
	"display_name": "Notion Tool",
	"version":      "1.0.0",
	"description":  "Notion 页面查询工具，演示凭据（API Token）的声明与使用",
	"author":       "Anna Developer",
	"credentials": []map[string]any{
		{
			"name":         "NOTION_TOKEN",
			"display_name": "Integration Token",
			"description":  "Notion → My Integrations → Create an integration to get the Internal Integration Secret",
			"required":     true,
			"sensitive":    true,
		},
	},
	"tools": []map[string]any{
		{
			"name":        "search_pages",
			"description": "搜索 Notion 页面",
			"parameters": []map[string]any{
				{
					"name":        "query",
					"type":        "string",
					"description": "搜索关键词",
					"required":    true,
				},
				{
					"name":        "limit",
					"type":        "integer",
					"description": "返回数量上限（1-20，默认 5）",
					"required":    false,
					"default":     5,
				},
			},
		},
		{
			"name":        "get_page",
			"description": "获取 Notion 页面内容",
			"parameters": []map[string]any{
				{
					"name":        "page_id",
					"type":        "string",
					"description": "Notion 页面 ID",
					"required":    true,
				},
			},
		},
	},
	"runtime": map[string]any{
		"type":        "binary",
		"min_version": "1.0.0",
	},
}

// ─── JSON-RPC 类型 ─────────────────────────────────────────────────

type rpcRequest struct {
	JSONRPC string         `json:"jsonrpc"`
	Method  string         `json:"method"`
	Params  map[string]any `json:"params,omitempty"`
	ID      any            `json:"id"`
}

type rpcResponse struct {
	JSONRPC string  `json:"jsonrpc"`
	ID      any     `json:"id"`
	Result  any     `json:"result,omitempty"`
	Error   *rpcErr `json:"error,omitempty"`
}

type rpcErr struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// ─── 凭据读取辅助函数 ──────────────────────────────────────────────
//
// 最佳实践：优先从 context.credentials 读取，回退到环境变量

// getCredential 按优先级获取凭据值
// 1. context.credentials（平台注入）
// 2. 环境变量（本地开发）
func getCredential(credentials map[string]any, name string, defaultValue string) string {
	if credentials != nil {
		if v, ok := credentials[name].(string); ok && v != "" {
			return v
		}
	}
	if v := os.Getenv(name); v != "" {
		return v
	}
	return defaultValue
}

// extractCredentials 从 invoke params 中提取凭据
func extractCredentials(params map[string]any) map[string]any {
	ctx, _ := params["context"].(map[string]any)
	if ctx == nil {
		return nil
	}
	creds, _ := ctx["credentials"].(map[string]any)
	return creds
}

// ─── 工具实现 ─────────────────────────────────────────────────────

func toolSearchPages(args map[string]any, credentials map[string]any) map[string]any {
	query, _ := args["query"].(string)
	limitF, _ := args["limit"].(float64)
	limit := int(limitF)
	if limit < 1 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}

	token := getCredential(credentials, "NOTION_TOKEN", "")
	if token == "" {
		return map[string]any{
			"error": "NOTION_TOKEN not configured",
			"hint": "配置方式（任选其一）：\n" +
				"  1. 平台统一授权: /settings/authorizations 页面配置\n" +
				"  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置\n" +
				"  3. 本地开发: NOTION_TOKEN=ntn_xxx go run credential_plugin.go",
		}
	}

	// ─── 实际调用示例（注释） ───
	// req, _ := http.NewRequest("POST", "https://api.notion.com/v1/search", body)
	// req.Header.Set("Authorization", "Bearer "+token)
	// req.Header.Set("Notion-Version", "2022-06-28")
	// ────────────────────────────

	// 模拟数据（演示用）
	pages := make([]map[string]any, 0, limit)
	for i := 0; i < limit; i++ {
		pages = append(pages, map[string]any{
			"id":          fmt.Sprintf("page-%d", i+1),
			"title":       fmt.Sprintf("Page matching '%s' #%d", query, i+1),
			"url":         fmt.Sprintf("https://notion.so/page-%d", i+1),
			"last_edited": time.Now().Add(-time.Duration(i) * 24 * time.Hour).UTC().Format(time.RFC3339),
		})
	}

	preview := token
	if len(token) > 8 {
		preview = token[:4] + "..." + token[len(token)-4:]
	} else {
		preview = "***"
	}

	return map[string]any{
		"query":            query,
		"count":            len(pages),
		"pages":            pages,
		"token_configured": true,
		"token_preview":    preview,
		"_note":            "This is simulated data for demonstration purposes",
	}
}

func toolGetPage(args map[string]any, credentials map[string]any) map[string]any {
	pageID, _ := args["page_id"].(string)
	if pageID == "" {
		return map[string]any{"error": "page_id is required"}
	}

	token := getCredential(credentials, "NOTION_TOKEN", "")
	if token == "" {
		return map[string]any{
			"error": "NOTION_TOKEN not configured",
			"hint": "配置方式（任选其一）：\n" +
				"  1. 平台统一授权: /settings/authorizations 页面配置\n" +
				"  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置\n" +
				"  3. 本地开发: NOTION_TOKEN=ntn_xxx go run credential_plugin.go",
		}
	}

	// 模拟数据
	return map[string]any{
		"id":               pageID,
		"title":            "Example Page",
		"content":          "This is the page content...",
		"last_edited":      time.Now().UTC().Format(time.RFC3339),
		"token_configured": true,
		"_note":            "This is simulated data for demonstration purposes",
	}
}

// ─── 请求处理 ─────────────────────────────────────────────────────

func handleRequest(req rpcRequest) rpcResponse {
	switch req.Method {
	case "describe":
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: manifest}

	case "invoke":
		return handleInvoke(req)

	case "health":
		creds := manifest["credentials"].([]map[string]any)
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
			"status":               "healthy",
			"timestamp":            time.Now().UTC().Format(time.RFC3339),
			"version":              manifest["version"],
			"tools_count":          len(manifest["tools"].([]map[string]any)),
			"credentials_declared": len(creds),
		}}

	default:
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32601,
			Message: fmt.Sprintf("Method not found: %s", req.Method),
		}}
	}
}

func handleInvoke(req rpcRequest) rpcResponse {
	toolName, _ := req.Params["tool"].(string)
	if toolName == "" {
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32602,
			Message: "Missing 'tool' in params",
		}}
	}

	args, _ := req.Params["arguments"].(map[string]any)
	if args == nil {
		args = map[string]any{}
	}

	// 从 context 中提取凭据（Agent 注入）
	credentials := extractCredentials(req.Params)

	var result map[string]any
	switch toolName {
	case "search_pages":
		result = toolSearchPages(args, credentials)
	case "get_page":
		result = toolGetPage(args, credentials)
	default:
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32601,
			Message: fmt.Sprintf("Unknown tool: %s", toolName),
			Data:    map[string]any{"available_tools": []string{"search_pages", "get_page"}},
		}}
	}

	return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
		"success": true,
		"data":    result,
		"tool":    toolName,
	}}
}

// ─── 主循环 ────────────────────────────────────────────────────────

func main() {
	fmt.Fprintln(os.Stderr, "🔌 Notion credential plugin started (Go)")
	fmt.Fprintf(os.Stderr, "   Tools: search_pages, get_page\n")
	fmt.Fprintf(os.Stderr, "   Credentials required: NOTION_TOKEN\n")
	fmt.Fprintf(os.Stderr, "   Platform: %s/%s\n", runtime.GOOS, runtime.GOARCH)

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		fmt.Fprintf(os.Stderr, "← %s\n", line)

		var req rpcRequest
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			resp := rpcResponse{JSONRPC: "2.0", ID: nil, Error: &rpcErr{
				Code:    -32700,
				Message: "Parse error",
			}}
			out, _ := json.Marshal(resp)
			fmt.Fprintln(os.Stdout, string(out))
			continue
		}

		resp := handleRequest(req)
		out, err := json.Marshal(resp)
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Failed to marshal response: %v\n", err)
			continue
		}
		fmt.Fprintln(os.Stdout, string(out))
		fmt.Fprintf(os.Stderr, "→ (sent)\n")
	}
}
