中文版本请参阅 [authorization.zh-CN.md](authorization.zh-CN.md)

# Platform Authorization

## Overview

Platform Authorization allows users to **connect third-party services once** in Anna Nexus (Google, Twitter/X, GitHub, Notion, Slack, etc.), and all Executa plugins that require credentials from these services automatically gain access — no per-plugin configuration needed.

```
┌───────────────────────────────────────────────────────────────┐
│                      Anna Nexus Platform                      │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          /settings/authorizations (User UI)              │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │  │
│  │  │  Google  │ │ Twitter  │ │  GitHub  │ │  Notion  │    │  │
│  │  │  (OAuth) │ │(API Key) │ │(API Key) │ │(API Key) │    │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │  │
│  └───────┼─────────────┼───────────┼─────────────┼─────────┘  │
│          ▼             ▼           ▼             ▼            │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │       AES-256-GCM Encrypted Storage                      │  │
│  │                (user_platform_credentials)                │  │
│  └───────────────────────────┬─────────────────────────────┘  │
│                              │ credential_mapping             │
│                              ▼                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │   resolve_credentials_for_plugin() — Credential Engine   │  │
│  │   Priority: Platform > Plugin-level > Environment Vars   │  │
│  └───────────────────────────┬─────────────────────────────┘  │
│                              │ context.credentials            │
│                              ▼                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ Gmail    │  │ Twitter  │  │ GitHub   │  │ Notion   │     │
│  │ Plugin   │  │ Plugin   │  │ Plugin   │  │ Plugin   │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
└───────────────────────────────────────────────────────────────┘
```

## Core Concepts

### Credentials Declaration

Plugins declare the credentials they need in the Manifest's `credentials` field:

```json
{
  "name": "gmail-tool",
  "credentials": [
    {
      "name": "GMAIL_ACCESS_TOKEN",
      "display_name": "Gmail Access Token",
      "description": "Google OAuth Access Token (auto-injected by platform)",
      "required": true,
      "sensitive": true
    }
  ],
  "tools": [...]
}
```

### Credential Mapping

The platform defines mappings from credential names to actual values through a **provider registry**:

```python
# Google OAuth provider credential mapping
credential_mapping = {
    "GOOGLE_ACCESS_TOKEN": "$access_token",  # Maps to OAuth access_token
    "GMAIL_ACCESS_TOKEN":  "$access_token",  # Same token, different alias
}

# Twitter API Key provider credential mapping
credential_mapping = {
    "TWITTER_API_KEY":          "TWITTER_API_KEY",          # Direct field mapping
    "TWITTER_API_SECRET":       "TWITTER_API_SECRET",
    "TWITTER_BEARER_TOKEN":     "TWITTER_BEARER_TOKEN",
}
```

Special values:
- `$access_token` — Auto-maps to OAuth2 access_token (automatically refreshes expired tokens)
- Plain strings — Map to user-provided fields in API Key mode

### Credential Resolution Priority

When the Agent calls a plugin, the credential resolution engine searches in this order:

1. **Platform unified credentials** — Configured at `/settings/authorizations` (highest priority)
2. **Plugin-level credentials** — Manually entered in per-plugin settings
3. **Environment variables** (local development only) — Plugin falls back to reading `os.environ`

## Workflow

### User Side

1. **Visit authorization page** — After logging into Nexus, go to `/settings/authorizations`
2. **Connect services** — Click the "Connect" button for the desired service:
   - **OAuth2 services** (e.g. Google): Redirects to third-party authorization page, select permission scopes, auto-redirects back after authorization
   - **API Key services** (e.g. Twitter/GitHub): Enter API Key / Token in a dialog
3. **View status** — After authorization, view connection status, associated email, permission scopes, etc.
4. **Manage authorizations** — Disconnect, refresh tokens, or view details at any time

### Plugin Developer Side

1. **Declare credentials** — List required credential names in the Manifest's `credentials`
2. **Receive credentials** — Read from `invoke` request's `params.context.credentials`
3. **Local fallback** — Fall back to reading environment variables during development

## Supported Providers

| Provider | Auth Type | Credential Fields | Compatible Plugins |
|----------|-----------|------------------|-------------------|
| **Google** | OAuth2 | access_token (10+ selectable scopes) | gws, gmail-tool, google-calendar, google-drive |
| **X (Twitter)** | API Key | API Key, API Secret, Access Token, Access Token Secret, Bearer Token | twitter-tool, x-tool, social-media |
| **GitHub** | API Key | Personal Access Token | github-tool, git-tool |
| **Notion** | API Key | Integration Token | notion-tool |
| **Slack** | API Key | Bot User OAuth Token | slack-tool |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/platform-credentials/providers` | List all supported providers |
| `GET` | `/api/v1/platform-credentials/my` | Current user's authorization status |
| `GET` | `/api/v1/platform-credentials/my/{provider_id}` | Authorization details for a specific provider |
| `GET` | `/api/v1/platform-credentials/oauth/{provider_id}/authorize` | Initiate OAuth authorization |
| `GET` | `/api/v1/platform-credentials/oauth/{provider_id}/callback` | OAuth callback |
| `PUT` | `/api/v1/platform-credentials/api-key/{provider_id}` | Set API Key |
| `GET` | `/api/v1/platform-credentials/api-key/{provider_id}/status` | API Key configuration status |
| `DELETE` | `/api/v1/platform-credentials/my/{provider_id}` | Disconnect authorization |
| `POST` | `/api/v1/platform-credentials/my/{provider_id}/refresh` | Refresh OAuth Token |

## Security Design

- **Encrypted storage** — All credentials use AES-256-GCM symmetric encryption; key source: `NEXUS_CREDENTIAL_KEY` env var > `SECRET_KEY` derivation > plaintext fallback (development only)
- **LLM isolation** — Credentials are injected via `context.credentials`; the LLM **cannot see** credential values and cannot leak them in conversations
- **Least privilege** — OAuth2 supports user-selective scope granting (e.g. grant Gmail read-only, not send permission)
- **Auto-refresh** — OAuth access_token is automatically refreshed using refresh_token when expired
- **Revocation support** — Token is revoked at the third-party when disconnecting

## Plugin Best Practices

### 1. Align Naming with credential_mapping

Plugin `credentials[].name` should align with keys in the platform provider registry's `credential_mapping` for automatic matching:

```json
// ✅ Good — Matches platform credential_mapping
{ "name": "TWITTER_API_KEY" }
{ "name": "GOOGLE_ACCESS_TOKEN" }
{ "name": "GITHUB_TOKEN" }

// ❌ Bad — Custom naming, platform cannot auto-map
{ "name": "MY_TWITTER_KEY" }
{ "name": "TOKEN_FOR_GOOGLE" }
```

### 2. Read Credentials from context First, Fall Back to Environment Variables

```python
def my_tool(city: str, *, credentials: dict | None = None) -> dict:
    creds = credentials or {}
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    if not api_key:
        return {"error": "WEATHER_API_KEY not configured"}
    # Use api_key ...
```

### 3. Never Expose Credentials in Tool Parameters

```json
// ✅ Good — Credentials in credentials declaration, invisible to LLM
{
  "credentials": [{ "name": "API_KEY", ... }],
  "tools": [{ "name": "search", "parameters": [{ "name": "query", ... }] }]
}

// ❌ Bad — Credentials as tool parameters, visible to LLM and may leak
{
  "tools": [{ "name": "search", "parameters": [{ "name": "api_key", ... }, { "name": "query", ... }] }]
}
```

### 4. Mark Sensitive Credentials

```json
{
  "name": "API_SECRET",
  "sensitive": true   // Not echoed in UI, encrypted storage
}
```

### 5. Provide Clear Acquisition Instructions

```json
{
  "name": "GITHUB_TOKEN",
  "display_name": "Personal Access Token",
  "description": "GitHub Settings → Developer Settings → Personal access tokens (fine-grained recommended)"
}
```

## Adding a New Provider

Simply append a registration in `platform_credential_providers.py` — no database schema changes required:

```python
_register(
    CredentialProviderDef(
        provider_id="my-service",
        name="My Service",
        icon="my-service",
        description="My Service API",
        website="https://my-service.com",
        auth_type="api_key",
        api_key_fields=[
            CredentialFieldDef(
                name="MY_SERVICE_TOKEN",
                display_name="API Token",
                description="Obtain from https://my-service.com/settings/api",
            ),
        ],
        credential_mapping={
            "MY_SERVICE_TOKEN": "MY_SERVICE_TOKEN",
        },
        compatible_tool_ids=["my-service-tool"],
    )
)
```
