#!/usr/bin/env python3
"""Executa Google OAuth Plugin Example — Gmail read access via platform OAuth credentials

This example demonstrates:
1. Declaring an **OAuth-sourced** credential (GMAIL_ACCESS_TOKEN) in the Manifest
2. Receiving the auto-injected OAuth access_token via context.credentials
3. Using the token to call Google Gmail API
4. The difference between API Key credentials and OAuth credentials from the plugin's perspective
   (spoiler: there is none — the plugin API is identical)

How it works (end-to-end):
  1. User authorizes Google in Nexus (/settings/authorizations), granting Gmail scopes
  2. Nexus stores the OAuth tokens (AES-256-GCM encrypted), auto-refreshes when expired
  3. Platform credential_mapping: "GMAIL_ACCESS_TOKEN" → "$access_token"
  4. When Agent invokes this plugin, resolved credentials are injected via context.credentials
  5. Plugin reads context.credentials["GMAIL_ACCESS_TOKEN"] — a valid OAuth access token

Credential resolution priority (three tiers):
  1. Platform unified credentials — Google OAuth token from /settings/authorizations
  2. Plugin-level credentials — manually entered access token (for testing)
  3. Environment variables — GMAIL_ACCESS_TOKEN env var for local development

Usage:
    python google_oauth_plugin.py

Local development (provide token via environment variable):
    GMAIL_ACCESS_TOKEN=ya29.xxx python google_oauth_plugin.py

Protocol requirements:
    - stdin:  Receives JSON-RPC requests (one JSON object per line)
    - stdout: Returns JSON-RPC responses (one JSON object per line)
    - stderr: Log output (does not interfere with protocol communication)
"""

import json
import os
import sys
from datetime import datetime, timezone


# ─── Manifest (Self-Description) ─────────────────────────────────────
#
# Key difference from API Key examples:
#   - credential name "GMAIL_ACCESS_TOKEN" aligns with the Google provider's
#     credential_mapping in Nexus, so the platform auto-injects the OAuth token.
#   - The plugin does NOT manage OAuth flow (authorize, token exchange, refresh).
#     Nexus handles all of that — the plugin just receives a ready-to-use access_token.
#
# Naming alignment:
#   GMAIL_ACCESS_TOKEN         → Google provider → $access_token (auto-mapped)
#   GOOGLE_ACCESS_TOKEN        → Google provider → $access_token (alternative name)
#   YOUTUBE_ACCESS_TOKEN       → Google provider → $access_token (YouTube alias)
#   GOOGLE_DOCS_ACCESS_TOKEN   → Google provider → $access_token (Docs alias)
#   GOOGLE_SHEETS_ACCESS_TOKEN → Google provider → $access_token (Sheets alias)
#   All resolve to the same OAuth access_token.

MANIFEST = {
    "name": "gmail-tool",
    "display_name": "Gmail Tool",
    "version": "1.0.0",
    "description": "Gmail message reader — demonstrates Google OAuth credential usage via platform authorization",
    "author": "Anna Developer",
    # ─── OAuth Credential Declaration ─────────────────────────────────
    # The credential name "GMAIL_ACCESS_TOKEN" matches the Google provider's
    # credential_mapping, enabling automatic injection of the OAuth access_token.
    #
    # Required Google OAuth scopes (user selects when authorizing):
    #   - https://www.googleapis.com/auth/gmail.readonly (for list_messages, get_message)
    #
    # The plugin does NOT need to specify scopes — that's configured in Nexus's
    # provider registry. The plugin just declares the credential name it needs.
    "credentials": [
        {
            "name": "GMAIL_ACCESS_TOKEN",
            "display_name": "Gmail Access Token",
            "description": (
                "Google OAuth Access Token — automatically provided by the platform "
                "when user authorizes Google at /settings/authorizations. "
                "Required scope: gmail.readonly"
            ),
            "required": True,
            "sensitive": True,
        },
    ],
    "tools": [
        {
            "name": "list_messages",
            "description": "List recent Gmail messages (subjects and senders)",
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "description": "Gmail search query (same syntax as Gmail search bar), e.g. 'is:unread', 'from:alice@example.com', 'subject:invoice'",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "max_results",
                    "type": "integer",
                    "description": "Maximum number of messages to return (1-20, default 10)",
                    "required": False,
                    "default": 10,
                },
            ],
        },
        {
            "name": "get_message",
            "description": "Get the full content of a specific Gmail message by ID",
            "parameters": [
                {
                    "name": "message_id",
                    "type": "string",
                    "description": "Gmail message ID (obtained from list_messages)",
                    "required": True,
                },
            ],
        },
    ],
    "runtime": {
        "type": "uv",
        "min_version": "0.1.0",
    },
}


# ─── Tool Implementation ─────────────────────────────────────────────


def tool_list_messages(
    query: str = "", max_results: int = 10, *, credentials: dict | None = None
) -> dict:
    """List recent Gmail messages

    The GMAIL_ACCESS_TOKEN is an OAuth2 access_token obtained through platform authorization.
    The platform handles the entire OAuth flow (authorization, token exchange, auto-refresh).
    This plugin just uses the token as a Bearer token in API requests.
    """
    creds = credentials or {}

    # Read OAuth token from context.credentials first, fall back to env var
    access_token = creds.get("GMAIL_ACCESS_TOKEN") or os.environ.get(
        "GMAIL_ACCESS_TOKEN"
    )

    if not access_token:
        return {
            "error": "GMAIL_ACCESS_TOKEN not configured",
            "hint": (
                "This plugin requires Google OAuth authorization.\n"
                "Configuration options (choose one):\n"
                "  1. Platform authorization (recommended): Go to /settings/authorizations, "
                "connect Google, and grant 'Gmail Read' scope\n"
                "  2. Plugin-level credential: Enter an OAuth access_token manually in plugin settings\n"
                "  3. Local development: GMAIL_ACCESS_TOKEN=ya29.xxx python google_oauth_plugin.py"
            ),
        }

    max_results = max(1, min(20, max_results))

    # ─── Actual Gmail API call (commented out) ───
    # import urllib.request
    #
    # url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    # url += f"?maxResults={max_results}"
    # if query:
    #     url += f"&q={urllib.parse.quote(query)}"
    #
    # req = urllib.request.Request(url)
    # req.add_header("Authorization", f"Bearer {access_token}")
    # req.add_header("Accept", "application/json")
    #
    # resp = urllib.request.urlopen(req)
    # data = json.loads(resp.read())
    # messages = data.get("messages", [])
    #
    # # For each message, fetch metadata
    # results = []
    # for msg in messages[:max_results]:
    #     msg_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=metadata"
    #     msg_req = urllib.request.Request(msg_url)
    #     msg_req.add_header("Authorization", f"Bearer {access_token}")
    #     msg_resp = urllib.request.urlopen(msg_req)
    #     msg_data = json.loads(msg_resp.read())
    #     headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
    #     results.append({
    #         "id": msg["id"],
    #         "subject": headers.get("Subject", "(no subject)"),
    #         "from": headers.get("From", ""),
    #         "date": headers.get("Date", ""),
    #         "snippet": msg_data.get("snippet", ""),
    #     })
    # ────────────────────────────

    # Simulated data (for demonstration)
    sample_messages = [
        {
            "id": "msg_18f1a2b3c4d5",
            "subject": "Weekly Team Standup Notes",
            "from": "alice@example.com",
            "date": "Mon, 14 Apr 2025 09:00:00 +0800",
            "snippet": "Here are the notes from today's standup meeting...",
            "labels": ["INBOX", "UNREAD"],
        },
        {
            "id": "msg_18f1a2b3c4d6",
            "subject": "Invoice #2025-042 for Project Alpha",
            "from": "billing@vendor.com",
            "date": "Sun, 13 Apr 2025 14:30:00 +0800",
            "snippet": "Please find attached the invoice for April...",
            "labels": ["INBOX"],
        },
        {
            "id": "msg_18f1a2b3c4d7",
            "subject": "Re: API Integration Review",
            "from": "bob@example.com",
            "date": "Sat, 12 Apr 2025 11:15:00 +0800",
            "snippet": "I've reviewed the PR and left some comments...",
            "labels": ["INBOX"],
        },
    ]

    messages = sample_messages[:max_results]
    if query:
        messages = [
            m
            for m in messages
            if query.lower() in m["subject"].lower()
            or query.lower() in m["from"].lower()
        ]

    return {
        "query": query or "(all messages)",
        "total": len(messages),
        "messages": messages,
        "token_configured": True,
        "token_preview": (
            f"{access_token[:8]}...{access_token[-4:]}"
            if len(access_token) > 12
            else "***"
        ),
        "_note": "This is simulated data for demonstration purposes",
    }


def tool_get_message(
    message_id: str, *, credentials: dict | None = None
) -> dict:
    """Get full content of a specific Gmail message"""
    creds = credentials or {}
    access_token = creds.get("GMAIL_ACCESS_TOKEN") or os.environ.get(
        "GMAIL_ACCESS_TOKEN"
    )

    if not access_token:
        return {
            "error": "GMAIL_ACCESS_TOKEN not configured",
            "hint": (
                "This plugin requires Google OAuth authorization.\n"
                "Go to /settings/authorizations, connect Google, "
                "and grant 'Gmail Read' scope."
            ),
        }

    if not message_id:
        return {"error": "message_id is required"}

    # ─── Actual Gmail API call (commented out) ───
    # import urllib.request
    #
    # url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full"
    # req = urllib.request.Request(url)
    # req.add_header("Authorization", f"Bearer {access_token}")
    # req.add_header("Accept", "application/json")
    #
    # resp = urllib.request.urlopen(req)
    # data = json.loads(resp.read())
    # ────────────────────────────

    # Simulated data
    return {
        "id": message_id,
        "thread_id": f"thread_{message_id}",
        "subject": "Weekly Team Standup Notes",
        "from": "alice@example.com",
        "to": "team@example.com",
        "date": "Mon, 14 Apr 2025 09:00:00 +0800",
        "body": (
            "Hi team,\n\n"
            "Here are the notes from today's standup:\n"
            "- Alice: Working on OAuth plugin examples\n"
            "- Bob: Reviewing API integration PR\n"
            "- Carol: Setting up CI/CD pipeline\n\n"
            "Best regards,\nAlice"
        ),
        "labels": ["INBOX", "UNREAD"],
        "token_configured": True,
        "_note": "This is simulated data for demonstration purposes",
    }


# ─── Tool Dispatch Table ─────────────────────────────────────────────

TOOL_DISPATCH = {
    "list_messages": tool_list_messages,
    "get_message": tool_get_message,
}


# ─── JSON-RPC Handling ───────────────────────────────────────────────


def make_response(id, result=None, error=None):
    """Construct a JSON-RPC 2.0 response"""
    resp = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def handle_describe(request_id):
    """Handle describe request — return the tool manifest (including OAuth credential declaration)"""
    return make_response(request_id, result=MANIFEST)


def handle_invoke(request_id, params):
    """Handle invoke request — execute a tool call

    For OAuth credentials, the injection format is identical to API Key:
    {
        "tool": "list_messages",
        "arguments": {"query": "is:unread", "max_results": 5},
        "context": {
            "credentials": {
                "GMAIL_ACCESS_TOKEN": "ya29.a0ARrdaM..."
            }
        }
    }

    The OAuth access_token is automatically refreshed by Nexus before injection.
    If the token has expired and refresh fails, the credential will not be present
    (or will contain an error indicator), and the plugin should return a helpful error.
    """
    tool_name = params.get("tool")
    arguments = params.get("arguments", {})

    # Extract credentials from context (injected by Agent)
    context = params.get("context", {})
    credentials = context.get("credentials")

    if not tool_name:
        return make_response(
            request_id,
            error={"code": -32602, "message": "Missing 'tool' in params"},
        )

    fn = TOOL_DISPATCH.get(tool_name)
    if fn is None:
        return make_response(
            request_id,
            error={
                "code": -32601,
                "message": f"Unknown tool: {tool_name}",
                "data": {"available_tools": list(TOOL_DISPATCH.keys())},
            },
        )

    try:
        result = fn(**arguments, credentials=credentials)
        return make_response(
            request_id,
            result={"success": True, "data": result, "tool": tool_name},
        )
    except TypeError as e:
        return make_response(
            request_id,
            error={"code": -32602, "message": f"Invalid parameters: {e}"},
        )
    except Exception as e:
        return make_response(
            request_id,
            error={"code": -32603, "message": f"Tool execution failed: {e}"},
        )


def handle_health(request_id):
    """Handle health request"""
    return make_response(
        request_id,
        result={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": MANIFEST["version"],
            "tools_count": len(MANIFEST["tools"]),
            "credentials_declared": len(MANIFEST.get("credentials", [])),
            "auth_type": "oauth2 (via platform)",
        },
    )


def handle_request(line: str) -> str:
    """Parse and handle a single JSON-RPC request"""
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return json.dumps(
            make_response(None, error={"code": -32700, "message": "Parse error"})
        )

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if method == "describe":
        response = handle_describe(request_id)
    elif method == "invoke":
        response = handle_invoke(request_id, params)
    elif method == "health":
        response = handle_health(request_id)
    else:
        response = make_response(
            request_id,
            error={"code": -32601, "message": f"Method not found: {method}"},
        )

    return json.dumps(response)


# ─── Main Loop (stdio JSON-RPC Service) ──────────────────────────────


def main():
    """Main entry point: reads JSON-RPC requests line by line from stdin."""
    print("🔌 Gmail OAuth credential plugin started", file=sys.stderr)
    print(f"   Tools: {list(TOOL_DISPATCH.keys())}", file=sys.stderr)
    print(
        f"   Credentials required: "
        f"{[c['name'] for c in MANIFEST.get('credentials', [])]}",
        file=sys.stderr,
    )
    print(
        "   Auth type: OAuth2 (via platform — plugin receives ready-to-use token)",
        file=sys.stderr,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        print(f"← {line}", file=sys.stderr)

        response = handle_request(line)

        # Response via stdout — must flush to avoid buffer blocking
        print(response, flush=True)

        print(f"→ {response}", file=sys.stderr)


if __name__ == "__main__":
    main()
