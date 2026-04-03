// Executa 插件示例 — Go 实现
//
// 这是一个完整的 Go Executa 插件示例，实现了系统信息查询和文件哈希工具。
// Go 天然编译为独立二进制，非常适合 Binary 分发方式。
//
// 运行方式：
//
//	go run .
//
// 构建：
//
//	go build -o dist/example-go-tool .
//
// 协议要求：
//   - stdin:  接收 JSON-RPC 请求（每行一个 JSON 对象）
//   - stdout: 返回 JSON-RPC 响应（每行一个 JSON 对象）
//   - stderr: 日志输出（不会干扰协议通信）
package main

import (
	"bufio"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strings"
	"time"
)

// ─── Manifest ──────────────────────────────────────────────────────

// Manifest 定义了插件的自描述清单
var manifest = map[string]any{
	"name":         "example-go-tool",
	"display_name": "Example Go Tool",
	"version":      "1.0.0",
	"description":  "一个 Go 示例工具，演示系统信息查询和哈希计算",
	"author":       "Anna Developer",
	"tools": []map[string]any{
		{
			"name":        "system_info",
			"description": "获取当前系统的 OS、架构、Go 版本等信息",
			"parameters":  []map[string]any{},
		},
		{
			"name":        "hash_text",
			"description": "计算文本的 SHA-256 或 MD5 哈希值",
			"parameters": []map[string]any{
				{
					"name":        "text",
					"type":        "string",
					"description": "要计算哈希的文本",
					"required":    true,
				},
				{
					"name":        "algorithm",
					"type":        "string",
					"description": "哈希算法: sha256 / md5（默认 sha256）",
					"required":    false,
					"default":     "sha256",
				},
			},
		},
		{
			"name":        "string_utils",
			"description": "字符串工具：统计长度、反转、重复",
			"parameters": []map[string]any{
				{
					"name":        "text",
					"type":        "string",
					"description": "输入文本",
					"required":    true,
				},
				{
					"name":        "operation",
					"type":        "string",
					"description": "操作: length / reverse / repeat / upper / lower",
					"required":    true,
				},
				{
					"name":        "count",
					"type":        "integer",
					"description": "repeat 操作的重复次数（默认 2）",
					"required":    false,
					"default":     2,
				},
			},
		},
		{
			"name":        "batch_hash",
			"description": "批量计算多段文本的哈希值（演示 array 参数用法）",
			"parameters": []map[string]any{
				{
					"name":        "texts",
					"type":        "array",
					"items":       map[string]any{"type": "string"},
					"description": "要计算哈希的文本列表",
					"required":    true,
				},
				{
					"name":        "algorithm",
					"type":        "string",
					"description": "哈希算法: sha256 / md5（默认 sha256）",
					"required":    false,
					"default":     "sha256",
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

// ─── 工具实现 ─────────────────────────────────────────────────────

func toolSystemInfo() map[string]any {
	hostname, _ := os.Hostname()
	return map[string]any{
		"os":         runtime.GOOS,
		"arch":       runtime.GOARCH,
		"go_version": runtime.Version(),
		"cpus":       runtime.NumCPU(),
		"hostname":   hostname,
		"pid":        os.Getpid(),
	}
}

func toolHashText(args map[string]any) map[string]any {
	text, _ := args["text"].(string)
	algorithm, ok := args["algorithm"].(string)
	if !ok || algorithm == "" {
		algorithm = "sha256"
	}

	var hashHex string
	switch algorithm {
	case "sha256":
		h := sha256.Sum256([]byte(text))
		hashHex = hex.EncodeToString(h[:])
	case "md5":
		h := md5.Sum([]byte(text))
		hashHex = hex.EncodeToString(h[:])
	default:
		return map[string]any{
			"error": fmt.Sprintf("Unsupported algorithm: %s. Available: sha256, md5", algorithm),
		}
	}

	return map[string]any{
		"hash":         hashHex,
		"algorithm":    algorithm,
		"input_length": len(text),
	}
}

func toolStringUtils(args map[string]any) map[string]any {
	text, _ := args["text"].(string)
	operation, _ := args["operation"].(string)
	countF, _ := args["count"].(float64) // JSON numbers → float64
	count := int(countF)
	if count < 1 {
		count = 2
	}
	if count > 100 {
		count = 100
	}

	switch operation {
	case "length":
		return map[string]any{
			"length":       len(text),
			"rune_count":   len([]rune(text)),
			"byte_count":   len([]byte(text)),
		}
	case "reverse":
		runes := []rune(text)
		for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
			runes[i], runes[j] = runes[j], runes[i]
		}
		return map[string]any{
			"original": text,
			"reversed": string(runes),
		}
	case "repeat":
		parts := make([]string, count)
		for i := range parts {
			parts[i] = text
		}
		return map[string]any{
			"result": strings.Join(parts, " "),
			"count":  count,
		}
	case "upper":
		return map[string]any{
			"original":    text,
			"transformed": strings.ToUpper(text),
		}
	case "lower":
		return map[string]any{
			"original":    text,
			"transformed": strings.ToLower(text),
		}
	default:
		return map[string]any{
			"error": fmt.Sprintf("Unknown operation: %s. Available: length, reverse, repeat, upper, lower", operation),
		}
	}
}

// ─── 请求处理 ─────────────────────────────────────────────────────

func toolBatchHash(args map[string]any) map[string]any {
	textsRaw, _ := args["texts"].([]any)
	algorithm, ok := args["algorithm"].(string)
	if !ok || algorithm == "" {
		algorithm = "sha256"
	}

	var results []map[string]any
	for _, raw := range textsRaw {
		text, _ := raw.(string)
		var hashHex string
		switch algorithm {
		case "sha256":
			h := sha256.Sum256([]byte(text))
			hashHex = hex.EncodeToString(h[:])
		case "md5":
			h := md5.Sum([]byte(text))
			hashHex = hex.EncodeToString(h[:])
		default:
			return map[string]any{
				"error": fmt.Sprintf("Unsupported algorithm: %s. Available: sha256, md5", algorithm),
			}
		}
		preview := text
		if len(preview) > 50 {
			preview = preview[:50]
		}
		results = append(results, map[string]any{
			"text_preview": preview,
			"hash":         hashHex,
			"algorithm":    algorithm,
		})
	}

	return map[string]any{
		"count":   len(results),
		"results": results,
	}
}

func handleRequest(req rpcRequest) rpcResponse {
	switch req.Method {
	case "describe":
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: manifest}

	case "invoke":
		return handleInvoke(req)

	case "health":
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
			"status":      "healthy",
			"timestamp":   time.Now().UTC().Format(time.RFC3339),
			"version":     manifest["version"],
			"tools_count": len(manifest["tools"].([]map[string]any)),
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

	var result map[string]any
	switch toolName {
	case "system_info":
		result = toolSystemInfo()
	case "hash_text":
		result = toolHashText(args)
	case "string_utils":
		result = toolStringUtils(args)
	case "batch_hash":
		result = toolBatchHash(args)
	default:
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32601,
			Message: fmt.Sprintf("Unknown tool: %s", toolName),
			Data:    map[string]any{"available_tools": []string{"system_info", "hash_text", "string_utils", "batch_hash"}},
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
	fmt.Fprintln(os.Stderr, "🔌 Example Go Executa plugin started")
	fmt.Fprintf(os.Stderr, "   Tools: system_info, hash_text, string_utils, batch_hash\n")
	fmt.Fprintf(os.Stderr, "   Platform: %s/%s\n", runtime.GOOS, runtime.GOARCH)

	scanner := bufio.NewScanner(os.Stdin)
	// 增大缓冲区以支持大型请求
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
		out, _ := json.Marshal(resp)
		fmt.Fprintln(os.Stdout, string(out))
		fmt.Fprintf(os.Stderr, "→ %s\n", string(out))
	}
}
