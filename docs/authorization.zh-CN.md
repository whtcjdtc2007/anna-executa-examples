For English version, see [authorization.md](authorization.md)

# 平台统一授权（Platform Authorization）

## 概述

平台统一授权允许用户在 Anna Nexus 中**一次性连接**第三方服务（Google、Twitter/X、GitHub、Notion、Slack 等），所有需要这些服务凭据的 Executa 插件自动获得访问权限，无需逐个插件重复配置。

```
┌───────────────────────────────────────────────────────────────┐
│                      Anna Nexus Platform                      │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │            /settings/authorizations (用户 UI)            │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │  │
│  │  │  Google  │ │ Twitter  │ │  GitHub  │ │  Notion  │    │  │
│  │  │  (OAuth) │ │(API Key) │ │(API Key) │ │(API Key) │    │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │  │
│  └───────┼─────────────┼───────────┼─────────────┼─────────┘  │
│          ▼             ▼           ▼             ▼            │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          AES-256-GCM 加密存储 (user_platform_credentials) │  │
│  └───────────────────────────┬─────────────────────────────┘  │
│                              │ credential_mapping             │
│                              ▼                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │      resolve_credentials_for_plugin() — 凭据解析引擎     │  │
│  │  优先级: 平台统一凭据 > 插件级手动凭据 > 环境变量         │  │
│  └───────────────────────────┬─────────────────────────────┘  │
│                              │ context.credentials            │
│                              ▼                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ Gmail    │  │ Twitter  │  │ GitHub   │  │ Notion   │     │
│  │ Plugin   │  │ Plugin   │  │ Plugin   │  │ Plugin   │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
└───────────────────────────────────────────────────────────────┘
```

## 核心概念

### 凭据声明（Credentials Declaration）

插件在 Manifest 的 `credentials` 字段声明自己需要哪些凭据：

```json
{
  "name": "gmail-tool",
  "credentials": [
    {
      "name": "GMAIL_ACCESS_TOKEN",
      "display_name": "Gmail Access Token",
      "description": "Google OAuth Access Token（由平台自动注入）",
      "required": true,
      "sensitive": true
    }
  ],
  "tools": [...]
}
```

### 凭据映射（Credential Mapping）

平台通过**提供商注册表**定义凭据名称到实际值的映射关系：

```python
# Google OAuth provider 的凭据映射
credential_mapping = {
    "GOOGLE_ACCESS_TOKEN":        "$access_token",  # 映射到 OAuth access_token
    "GMAIL_ACCESS_TOKEN":         "$access_token",  # 同一个 token，不同别名
    "GOOGLE_WORKSPACE_CLI_TOKEN": "$access_token",  # Google Workspace CLI 别名
    "YOUTUBE_ACCESS_TOKEN":       "$access_token",  # YouTube Data API
    "GOOGLE_DOCS_ACCESS_TOKEN":   "$access_token",  # Google Docs API
    "GOOGLE_SHEETS_ACCESS_TOKEN": "$access_token",  # Google Sheets API
}

# Twitter / X OAuth2 provider 的凭据映射
credential_mapping = {
    "TWITTER_ACCESS_TOKEN": "$access_token",  # 映射到 OAuth access_token
    "X_ACCESS_TOKEN":       "$access_token",  # 别名
}
```

特殊值说明：
- `$access_token` — 自动映射到 OAuth2 的 access_token（会自动刷新过期 token）
- 普通字符串 — 映射到 API Key 模式下用户填写的字段

### 凭据解析优先级

当 Agent 调用插件时，凭据解析引擎按以下优先级查找：

1. **平台统一凭据** — 用户在 `/settings/authorizations` 配置的（最高优先级）
2. **插件级凭据** — 用户在单个插件的配置中手动填写的
3. **环境变量**（仅本地开发）— 插件自行回退读取 `os.environ`

## 使用流程

### 用户侧

1. **访问授权页面** — 登录 Nexus 后访问 `/settings/authorizations`
2. **连接服务** — 点击对应服务的「连接」按钮：
   - **OAuth2 服务**（如 Google、X/Twitter）：跳转到第三方授权页面，选择权限范围（scopes），授权后自动跳回
   - **API Key 服务**（如 GitHub/Notion）：在弹窗中填写 API Key / Token
3. **查看状态** — 授权完成后可看到连接状态、关联邮箱、权限范围等
4. **管理授权** — 可随时断开连接、刷新 Token、查看详情

### 插件开发者侧

1. **声明凭据** — 在 Manifest 的 `credentials` 中列出所需凭据名称
2. **接收凭据** — 从 `invoke` 请求的 `params.context.credentials` 读取
3. **本地回退** — 开发调试时回退读取环境变量

## 已支持的提供商

| Provider | Auth Type | 凭据字段 | 推荐凭据名称 |
|----------|-----------|---------|---------------|
| **Google** | OAuth2 | access_token（18 scopes 可选） | `GOOGLE_ACCESS_TOKEN`, `GMAIL_ACCESS_TOKEN`, `YOUTUBE_ACCESS_TOKEN`, `GOOGLE_DOCS_ACCESS_TOKEN`, `GOOGLE_SHEETS_ACCESS_TOKEN` |
| **X (Twitter)** | OAuth2 | access_token（20 scopes 可选，PKCE） | `TWITTER_ACCESS_TOKEN`, `X_ACCESS_TOKEN` |
| **GitHub** | API Key | Personal Access Token | `GITHUB_TOKEN`, `GITHUB_ACCESS_TOKEN` |
| **Notion** | API Key | Integration Token | `NOTION_TOKEN`, `NOTION_API_KEY` |
| **Slack** | API Key | Bot User OAuth Token | `SLACK_BOT_TOKEN`, `SLACK_TOKEN` |

> **注意：** 任何声明了与 provider 的 `credential_mapping` 匹配的凭据名称的插件，都会自动获得凭据注入——没有 tool_id 白名单限制。

## API 端点

| Method | Endpoint | 说明 |
|--------|----------|------|
| `GET` | `/api/v1/platform-credentials/providers` | 列出所有支持的提供商 |
| `GET` | `/api/v1/platform-credentials/my` | 当前用户所有授权状态 |
| `GET` | `/api/v1/platform-credentials/my/{provider_id}` | 指定提供商的授权详情 |
| `GET` | `/api/v1/platform-credentials/oauth/{provider_id}/authorize` | 发起 OAuth 授权 |
| `GET` | `/api/v1/platform-credentials/oauth/{provider_id}/callback` | OAuth 回调 |
| `PUT` | `/api/v1/platform-credentials/api-key/{provider_id}` | 设置 API Key |
| `GET` | `/api/v1/platform-credentials/api-key/{provider_id}/status` | API Key 配置状态 |
| `DELETE` | `/api/v1/platform-credentials/my/{provider_id}` | 断开授权 |
| `POST` | `/api/v1/platform-credentials/my/{provider_id}/refresh` | 刷新 OAuth Token |

## 安全设计

- **加密存储** — 所有凭据使用 AES-256-GCM 对称加密，密钥来源：`NEXUS_CREDENTIAL_KEY` 环境变量 > `SECRET_KEY` 派生 > 明文回退（仅开发环境）
- **LLM 隔离** — 凭据通过 `context.credentials` 注入，LLM **看不到**凭据值，无法在对话中泄露
- **最小权限** — OAuth2 支持用户选择性授予 scopes（如只给 Gmail 读取权限，不给发送权限）
- **自动刷新** — OAuth access_token 过期时自动使用 refresh_token 刷新
- **撤销支持** — 断开连接时同时向第三方撤销 token

## 插件最佳实践

### 1. 使用 credential_mapping 对齐命名

插件的 `credentials[].name` 应与平台提供商注册表中的 `credential_mapping` 键对齐，这样平台能自动匹配：

```json
// ✅ 好 — 与平台 credential_mapping 一致
{ "name": "TWITTER_ACCESS_TOKEN" }
{ "name": "GOOGLE_ACCESS_TOKEN" }
{ "name": "GITHUB_TOKEN" }

// ❌ 差 — 自定义命名，平台无法自动映射
{ "name": "MY_TWITTER_KEY" }
{ "name": "TOKEN_FOR_GOOGLE" }
```

### 2. 凭据优先从 context 读取，回退到环境变量

```python
def my_tool(city: str, *, credentials: dict | None = None) -> dict:
    creds = credentials or {}
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    if not api_key:
        return {"error": "WEATHER_API_KEY not configured"}
    # 使用 api_key ...
```

### 3. 不要在工具参数中暴露凭据

```json
// ✅ 好 — 凭据在 credentials 声明中，LLM 看不到
{
  "credentials": [{ "name": "API_KEY", ... }],
  "tools": [{ "name": "search", "parameters": [{ "name": "query", ... }] }]
}

// ❌ 差 — 凭据作为工具参数，LLM 可见且可能泄露
{
  "tools": [{ "name": "search", "parameters": [{ "name": "api_key", ... }, { "name": "query", ... }] }]
}
```

### 4. 标记敏感凭据

```json
{
  "name": "API_SECRET",
  "sensitive": true   // UI 不回显，加密存储
}
```

### 5. 提供清晰的获取说明

```json
{
  "name": "GITHUB_TOKEN",
  "display_name": "Personal Access Token",
  "description": "GitHub Settings → Developer Settings → Personal access tokens (fine-grained recommended)"
}
```

## 扩展新的 Provider

在 `platform_credential_providers.py` 中追加注册即可，无需修改数据库 schema：

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
                description="从 https://my-service.com/settings/api 获取",
            ),
        ],
        credential_mapping={
            "MY_SERVICE_TOKEN": "MY_SERVICE_TOKEN",
        },
    )
)
```
