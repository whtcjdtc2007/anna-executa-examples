// Executa Plugin Example — Go Implementation
//
// This is a complete Go Executa plugin example that implements system info queries and file hashing tools.
// Go compiles natively to standalone binaries, making it ideal for binary distribution.
//
// Run:
//
//	go run .
//
// Build:
//
//	go build -o dist/example-go-tool .
//
// Protocol requirements:
//   - stdin:  Receives JSON-RPC requests (one JSON object per line)
//   - stdout: Returns JSON-RPC responses (one JSON object per line)
//   - stderr: Log output (does not interfere with protocol communication)
//
// ⚠️  CRITICAL — the plugin process must be LONG-RUNNING:
//   - Loop on `for scanner.Scan()` until stdin EOF (the Agent closes stdin
//     to shut you down). NEVER `os.Exit(0)` after handling a single request.
//   - Use `bufio.NewWriter(os.Stdout)` + explicit `Flush()` after each response,
//     or write directly with `os.Stdout.Write` (unbuffered).
//     A one-shot process passes `describe` once and then shows up as **Stopped**
//     in the Agent UI forever, paying a fresh cold-start on every invoke.
package main

import (
	"bufio"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Maximum single stdio message size in bytes; messages exceeding this threshold use file transport
const maxStdioMessageBytes = 512 * 1024

// ─── Manifest ──────────────────────────────────────────────────────

// Manifest defines the plugin's self-describing manifest
var manifest = map[string]any{
	"name":         "example-go-tool",
	"display_name": "Example Go Tool",
	"version":      "1.0.0",
	"description":  "A Go example tool demonstrating system info queries and hash computation",
	"author":       "Anna Developer",
	"tools": []map[string]any{
		{
			"name":        "system_info",
			"description": "Get current system OS, architecture, Go version, and other info",
			"parameters":  []map[string]any{},
		},
		{
			"name":        "hash_text",
			"description": "Compute the SHA-256 or MD5 hash of text",
			"parameters": []map[string]any{
				{
					"name":        "text",
					"type":        "string",
					"description": "The text to hash", "required": true,
				},
				{
					"name":        "algorithm",
					"type":        "string",
					"description": "Hash algorithm: sha256 / md5 (default sha256)",
					"required":    false,
					"default":     "sha256",
				},
			},
		},
		{
			"name":        "string_utils",
			"description": "String utilities: count length, reverse, repeat",
			"parameters": []map[string]any{
				{
					"name":        "text",
					"type":        "string",
					"description": "Input text",
					"required":    true,
				},
				{
					"name":        "operation",
					"type":        "string",
					"description": "Operation: length / reverse / repeat / upper / lower",
					"required":    true,
				},
				{
					"name":        "count",
					"type":        "integer",
					"description": "Repeat count for the repeat operation (default 2)",
					"required":    false,
					"default":     2,
				},
			},
		},
		{
			"name":        "batch_hash",
			"description": "Batch compute hashes for multiple texts (demonstrates array parameter usage)",
			"parameters": []map[string]any{
				{
					"name":        "texts",
					"type":        "array",
					"items":       map[string]any{"type": "string"},
					"description": "List of texts to hash",
					"required":    true,
				},
				{
					"name":        "algorithm",
					"type":        "string",
					"description": "Hash algorithm: sha256 / md5 (default sha256)",
					"required":    false,
					"default":     "sha256",
				},
			},
		},
		{
			"name":        "generate_dataset",
			"description": "Generate a mock dataset (can produce large responses, demonstrating file transport)",
			"parameters": []map[string]any{
				{
					"name":        "rows",
					"type":        "integer",
					"description": "Number of data rows to generate (1-100000; file transport is triggered above ~5000 rows)",
					"required":    false,
					"default":     100,
				},
				{
					"name":        "columns",
					"type":        "array",
					"items":       map[string]any{"type": "string"},
					"description": "List of column names to include; options: id / name / email / score / timestamp / description",
					"required":    false,
				},
			},
		},
	},
	"runtime": map[string]any{
		"type":        "binary",
		"min_version": "1.0.0",
	},
}

// ─── JSON-RPC Types ─────────────────────────────────────────────────

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

// fileTransportPointer is a lightweight pointer sent via stdout during file transport
type fileTransportPointer struct {
	JSONRPC       string `json:"jsonrpc"`
	ID            any    `json:"id"`
	FileTransport string `json:"__file_transport"`
}

// ─── Tool Implementations ─────────────────────────────────────────

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
			"length":     len(text),
			"rune_count": len([]rune(text)),
			"byte_count": len([]byte(text)),
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

// ─── Request Handling ─────────────────────────────────────────────────────

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

var (
	firstNames = []string{"Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
		"Grace", "Hank", "Ivy", "Jack", "Karen", "Leo"}
	lastNames = []string{"Smith", "Johnson", "Williams", "Brown", "Jones",
		"Garcia", "Miller", "Davis", "Wilson", "Taylor"}
	loremWords = []string{"lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
		"adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
		"incididunt", "ut", "labore", "et", "dolore", "magna"}
	availableCols = map[string]bool{
		"id": true, "name": true, "email": true,
		"score": true, "timestamp": true, "description": true,
	}
)

func toolGenerateDataset(args map[string]any) map[string]any {
	rowsF, _ := args["rows"].(float64)
	rows := int(rowsF)
	if rows < 1 {
		rows = 100
	}
	if rows > 100000 {
		rows = 100000
	}

	// Parse column names
	colsRaw, _ := args["columns"].([]any)
	var columns []string
	for _, c := range colsRaw {
		if s, ok := c.(string); ok && availableCols[s] {
			columns = append(columns, s)
		}
	}
	if len(columns) == 0 {
		columns = []string{"id", "name", "email", "score"}
	}

	rng := rand.New(rand.NewSource(42)) //nolint:gosec // fixed seed for reproducibility

	dataset := make([]map[string]any, 0, rows)
	for i := 0; i < rows; i++ {
		row := map[string]any{}
		for _, col := range columns {
			switch col {
			case "id":
				row["id"] = i + 1
			case "name":
				row["name"] = firstNames[rng.Intn(len(firstNames))] + " " +
					lastNames[rng.Intn(len(lastNames))]
			case "email":
				n := strings.ToLower(firstNames[rng.Intn(len(firstNames))] + "." +
					lastNames[rng.Intn(len(lastNames))])
				row["email"] = n + "@example.com"
			case "score":
				row["score"] = float64(rng.Intn(10001)) / 100.0
			case "timestamp":
				ts := int64(1700000000 + rng.Intn(10000001))
				row["timestamp"] = time.Unix(ts, 0).UTC().Format(time.RFC3339)
			case "description":
				wc := 10 + rng.Intn(21)
				words := make([]string, wc)
				for w := 0; w < wc; w++ {
					words[w] = loremWords[rng.Intn(len(loremWords))]
				}
				row["description"] = strings.Join(words, " ")
			}
		}
		dataset = append(dataset, row)
	}

	// Estimate response size
	sampleJSON, _ := json.Marshal(dataset[:1])
	estimatedBytes := len(sampleJSON) * rows

	return map[string]any{
		"rows":            rows,
		"columns":         columns,
		"estimated_bytes": estimatedBytes,
		"file_transport":  estimatedBytes > maxStdioMessageBytes,
		"dataset":         dataset,
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
	case "generate_dataset":
		result = toolGenerateDataset(args)
	default:
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32601,
			Message: fmt.Sprintf("Unknown tool: %s", toolName),
			Data:    map[string]any{"available_tools": []string{"system_info", "hash_text", "string_utils", "batch_hash", "generate_dataset"}},
		}}
	}

	return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
		"success": true,
		"data":    result,
		"tool":    toolName,
	}}
}

// ─── Response Sending (with file transport support) ──────────────────

// sendResponse sends a JSON-RPC response, automatically using file transport for large results.
// When the serialized JSON exceeds maxStdioMessageBytes, the full response is written
// to a temporary file and only a lightweight pointer containing the file path is sent via stdout.
// The agent will automatically delete the temporary file after reading it.
func sendResponse(resp rpcResponse) {
	out, err := json.Marshal(resp)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Failed to marshal response: %v\n", err)
		return
	}

	if len(out) > maxStdioMessageBytes {
		// Write to temporary file
		tmpPath := filepath.Join(os.TempDir(),
			fmt.Sprintf("executa-resp-%d.json", time.Now().UnixNano()))
		if writeErr := os.WriteFile(tmpPath, out, 0600); writeErr != nil {
			fmt.Fprintf(os.Stderr, "❌ Failed to write file transport: %v\n", writeErr)
			// Fall back to direct output
			fmt.Fprintln(os.Stdout, string(out))
			return
		}

		// Send file pointer
		pointer := fileTransportPointer{
			JSONRPC:       "2.0",
			ID:            resp.ID,
			FileTransport: tmpPath,
		}
		ptrOut, _ := json.Marshal(pointer)
		fmt.Fprintf(os.Stderr, "📦 Response too large (%d bytes), using file transport: %s\n",
			len(out), tmpPath)
		fmt.Fprintln(os.Stdout, string(ptrOut))
	} else {
		fmt.Fprintln(os.Stdout, string(out))
	}
}

// ─── Main Loop ────────────────────────────────────────────────────────

func main() {
	fmt.Fprintln(os.Stderr, "🔌 Example Go Executa plugin started")
	fmt.Fprintf(os.Stderr, "   Tools: system_info, hash_text, string_utils, batch_hash, generate_dataset\n")
	fmt.Fprintf(os.Stderr, "   Platform: %s/%s\n", runtime.GOOS, runtime.GOARCH)

	scanner := bufio.NewScanner(os.Stdin)
	// Increase buffer size to support large requests
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
		sendResponse(resp)
		fmt.Fprintf(os.Stderr, "→ (sent)\n")
	}
}
