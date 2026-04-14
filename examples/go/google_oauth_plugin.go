// Executa Google OAuth Plugin Example — Google Drive via platform OAuth credentials (Go)
//
// Demonstrates:
//  1. Declaring an OAuth-sourced credential (GOOGLE_ACCESS_TOKEN) in the Manifest
//  2. Receiving the auto-injected OAuth access_token via context.credentials
//  3. Using the token to call Google Drive API
//  4. The plugin experience is identical to API Key — OAuth complexity is handled by Nexus
//
// How it works (end-to-end):
//  1. User authorizes Google in Nexus (/settings/authorizations), granting Drive scopes
//  2. Nexus stores tokens (AES-256-GCM encrypted), auto-refreshes when expired
//  3. credential_mapping: "GOOGLE_ACCESS_TOKEN" → "$access_token"
//  4. Agent invokes this plugin → resolved credentials injected via context.credentials
//  5. Plugin reads context.credentials["GOOGLE_ACCESS_TOKEN"] — a valid OAuth access token
//
// Usage:
//
//	go run google_oauth_plugin.go
//
// Local development:
//
//	GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go
//
// Build:
//
//	go build -o dist/google-drive-tool google_oauth_plugin.go
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
// The credential name "GOOGLE_ACCESS_TOKEN" aligns with the Google
// provider's credential_mapping in Nexus, enabling auto-injection.
//
// Required Google OAuth scopes (user selects when authorizing):
//   - https://www.googleapis.com/auth/drive.readonly (for list_files, get_file)

var manifest = map[string]any{
	"name":         "google-drive-tool",
	"display_name": "Google Drive Tool",
	"version":      "1.0.0",
	"description":  "Google Drive file browser — demonstrates OAuth credential usage via platform authorization",
	"author":       "Anna Developer",
	// ─── OAuth Credential Declaration ─────────────────────────────
	// "GOOGLE_ACCESS_TOKEN" maps to "$access_token" in the Google provider.
	// The platform handles OAuth flow, token exchange, and auto-refresh.
	// This plugin just receives a ready-to-use Bearer token.
	"credentials": []map[string]any{
		{
			"name":         "GOOGLE_ACCESS_TOKEN",
			"display_name": "Google Access Token",
			"description":  "Google OAuth Access Token — automatically provided by the platform when user authorizes Google at /settings/authorizations. Required scope: drive.readonly",
			"required":     true,
			"sensitive":    true,
		},
	},
	"tools": []map[string]any{
		{
			"name":        "list_files",
			"description": "List files in Google Drive",
			"parameters": []map[string]any{
				{
					"name":        "query",
					"type":        "string",
					"description": "Drive search query (same syntax as Drive search), e.g. \"name contains 'report'\", \"mimeType='application/pdf'\"",
					"required":    false,
					"default":     "",
				},
				{
					"name":        "max_results",
					"type":        "integer",
					"description": "Maximum number of files to return (1-20, default 10)",
					"required":    false,
					"default":     10,
				},
			},
		},
		{
			"name":        "get_file",
			"description": "Get metadata and details of a specific Google Drive file",
			"parameters": []map[string]any{
				{
					"name":        "file_id",
					"type":        "string",
					"description": "Google Drive file ID (obtained from list_files)",
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

// ─── JSON-RPC types ────────────────────────────────────────────────

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

// ─── Credential Helper ─────────────────────────────────────────────

// getCredential resolves a credential by priority: context > env > default
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

// extractCredentials extracts credentials from invoke params.context
func extractCredentials(params map[string]any) map[string]any {
	ctx, _ := params["context"].(map[string]any)
	if ctx == nil {
		return nil
	}
	creds, _ := ctx["credentials"].(map[string]any)
	return creds
}

// tokenPreview returns a safe preview of a token for logging
func tokenPreview(token string) string {
	if len(token) > 12 {
		return token[:8] + "..." + token[len(token)-4:]
	}
	return "***"
}

// ─── Tool Implementation ───────────────────────────────────────────

func toolListFiles(args map[string]any, credentials map[string]any) map[string]any {
	query, _ := args["query"].(string)
	maxF, _ := args["max_results"].(float64)
	maxResults := int(maxF)
	if maxResults < 1 {
		maxResults = 10
	}
	if maxResults > 20 {
		maxResults = 20
	}

	token := getCredential(credentials, "GOOGLE_ACCESS_TOKEN", "")
	if token == "" {
		return map[string]any{
			"error": "GOOGLE_ACCESS_TOKEN not configured",
			"hint": "This plugin requires Google OAuth authorization.\n" +
				"Configuration options (choose one):\n" +
				"  1. Platform authorization (recommended): Go to /settings/authorizations,\n" +
				"     connect Google, and grant 'Drive Read' scope\n" +
				"  2. Plugin-level credential: Enter an OAuth access_token in plugin settings\n" +
				"  3. Local development: GOOGLE_ACCESS_TOKEN=ya29.xxx go run google_oauth_plugin.go",
		}
	}

	// ─── Actual Drive API call (commented out) ───
	// req, _ := http.NewRequest("GET", "https://www.googleapis.com/drive/v3/files", nil)
	// req.Header.Set("Authorization", "Bearer "+token)
	// req.Header.Set("Accept", "application/json")
	// q := req.URL.Query()
	// q.Set("pageSize", fmt.Sprintf("%d", maxResults))
	// q.Set("fields", "files(id,name,mimeType,size,modifiedTime,webViewLink)")
	// if query != "" {
	//     q.Set("q", query)
	// }
	// req.URL.RawQuery = q.Encode()
	// client := &http.Client{}
	// resp, err := client.Do(req)
	// ────────────────────────────

	// Simulated data (for demonstration)
	type fileEntry struct {
		name     string
		mimeType string
		size     string
	}
	sampleFiles := []fileEntry{
		{"Q1 Revenue Report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "245760"},
		{"Product Roadmap 2025.pdf", "application/pdf", "1048576"},
		{"Meeting Notes - Apr 14.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "32768"},
		{"Architecture Diagram.png", "image/png", "524288"},
		{"API Spec v2.json", "application/json", "8192"},
	}

	files := make([]map[string]any, 0, maxResults)
	for i := 0; i < maxResults && i < len(sampleFiles); i++ {
		f := sampleFiles[i]
		if query != "" && !strings.Contains(strings.ToLower(f.name), strings.ToLower(query)) {
			continue
		}
		files = append(files, map[string]any{
			"id":           fmt.Sprintf("file_%d_%d", time.Now().UnixMilli(), i),
			"name":         f.name,
			"mimeType":     f.mimeType,
			"size":         f.size,
			"modifiedTime": time.Now().Add(-time.Duration(i) * 24 * time.Hour).UTC().Format(time.RFC3339),
			"webViewLink":  fmt.Sprintf("https://drive.google.com/file/d/simulated_%d/view", i),
		})
	}

	return map[string]any{
		"query":            query,
		"total":            len(files),
		"files":            files,
		"token_configured": true,
		"token_preview":    tokenPreview(token),
		"_note":            "This is simulated data for demonstration purposes",
	}
}

func toolGetFile(args map[string]any, credentials map[string]any) map[string]any {
	fileID, _ := args["file_id"].(string)
	if fileID == "" {
		return map[string]any{"error": "file_id is required"}
	}

	token := getCredential(credentials, "GOOGLE_ACCESS_TOKEN", "")
	if token == "" {
		return map[string]any{
			"error": "GOOGLE_ACCESS_TOKEN not configured",
			"hint": "This plugin requires Google OAuth authorization.\n" +
				"Go to /settings/authorizations, connect Google,\n" +
				"and grant 'Drive Read' scope.",
		}
	}

	// ─── Actual Drive API call (commented out) ───
	// url := fmt.Sprintf("https://www.googleapis.com/drive/v3/files/%s?fields=*", fileID)
	// req, _ := http.NewRequest("GET", url, nil)
	// req.Header.Set("Authorization", "Bearer "+token)
	// ────────────────────────────

	// Simulated data
	return map[string]any{
		"id":               fileID,
		"name":             "Q1 Revenue Report.xlsx",
		"mimeType":         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"size":             "245760",
		"modifiedTime":     time.Now().UTC().Format(time.RFC3339),
		"createdTime":      time.Now().Add(-72 * time.Hour).UTC().Format(time.RFC3339),
		"webViewLink":      fmt.Sprintf("https://drive.google.com/file/d/%s/view", fileID),
		"owners":           []string{"user@example.com"},
		"shared":           true,
		"token_configured": true,
		"_note":            "This is simulated data for demonstration purposes",
	}
}

// ─── Request Handling ──────────────────────────────────────────────

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
			"auth_type":            "oauth2 (via platform)",
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

	// Extract credentials from context (injected by Agent)
	credentials := extractCredentials(req.Params)

	var result map[string]any
	switch toolName {
	case "list_files":
		result = toolListFiles(args, credentials)
	case "get_file":
		result = toolGetFile(args, credentials)
	default:
		return rpcResponse{JSONRPC: "2.0", ID: req.ID, Error: &rpcErr{
			Code:    -32601,
			Message: fmt.Sprintf("Unknown tool: %s", toolName),
			Data:    map[string]any{"available_tools": []string{"list_files", "get_file"}},
		}}
	}

	return rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
		"success": true,
		"data":    result,
		"tool":    toolName,
	}}
}

// ─── Main Loop ─────────────────────────────────────────────────────

func main() {
	fmt.Fprintln(os.Stderr, "🔌 Google Drive OAuth credential plugin started (Go)")
	fmt.Fprintf(os.Stderr, "   Tools: list_files, get_file\n")
	fmt.Fprintf(os.Stderr, "   Credentials required: GOOGLE_ACCESS_TOKEN\n")
	fmt.Fprintf(os.Stderr, "   Auth type: OAuth2 (via platform — plugin receives ready-to-use token)\n")
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
