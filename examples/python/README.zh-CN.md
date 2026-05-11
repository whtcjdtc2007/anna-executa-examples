For English version, see [README.md](README.md)

# Python Executa 插件示例

## 概述

每个示例都是**独立、自包含的子目录**，拥有自己的 `pyproject.toml`、插件源码
以及 PyInstaller spec 文件。这与 Go 端 `sampling-tool/` 的布局保持一致，使得
每个示例都可以被单独安装、分发或打包。

| 示例 | 子目录 | 说明 |
|------|--------|------|
| **基础插件** | [`basic-tool/`](basic-tool/) | 文本处理工具集（`word_count`、`text_transform`、`batch_word_count`）。 |
| **凭据插件** | [`credential-tool/`](credential-tool/) | 天气查询工具，演示声明 API Key 凭据并通过平台统一授权消费。 |
| **Google OAuth 插件** | [`google-oauth-tool/`](google-oauth-tool/) | Gmail 邮件查询工具，演示通过平台注入 Google OAuth2 访问令牌（OAuth 流程不在插件中）。 |
| **Sampling 插件（v2）** | [`sampling-summarizer/`](sampling-summarizer/) | 文本摘要器，通过反向 `sampling/createMessage` 请 host 代理完成一次 LLM 推理（插件无需 API key，模型选择/计费/配额均由 host 接管）。详见 [docs/sampling.zh-CN.md](../../docs/sampling.zh-CN.md)。 |
| **Storage 插件（v2）** | [`storage-notebook/`](storage-notebook/) | 笔记本 + 附件上传示例，通过反向 `storage/*`、`files/*` RPC 使用 Anna Persistent Storage（按用户/应用维度的 KV，配合宿主预签名 URL 的两步式对象上传）。详见 [docs/persistent-storage.zh-CN.md](../../docs/persistent-storage.zh-CN.md)。 |

## 子目录约定

所有示例遵循相同的目录结构：

```
<example-subdir>/
  pyproject.toml          # name、scripts、构建配置
  <plugin>.py             # 插件源码 —— 单文件、自包含
  pyinstaller.spec        # PyInstaller --onefile 配置（产物名）
```

顶层共享：

```
examples/python/
  README.md / README.zh-CN.md
  build_binary.sh         # 构建全部示例；带子目录参数则只构建指定示例
```

## 运行方式

### 直接运行（开发/调试）

```bash
cd basic-tool
python example_plugin.py

# 在另一个终端
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null
```

其他示例同理 —— `cd <subdir>` 后直接运行那个单 `.py` 文件即可。每个子目录
里 `.py` 文件顶部的 `MANIFEST` 常量列出了该插件的工具名。

### 通过 uv 安装（推荐的 Local 分发方式）

每个子目录都是独立的可安装包。按需安装，互不冲突：

```bash
cd basic-tool && uv tool install . && example-text-tool
cd credential-tool && uv tool install . && weather-tool
cd google-oauth-tool && uv tool install . && gmail-tool
cd sampling-summarizer && uv tool install . && sampling-summarizer
cd storage-notebook && uv tool install . && storage-notebook
```

### 通过 pipx 安装

```bash
cd basic-tool && pipx install . && example-text-tool
```

## 构建为独立二进制

共享脚本 `build_binary.sh` 位于本目录根。它会自动发现插件子目录（同时含
`pyproject.toml` 与至少一个 `*.py`），并分发到每个子目录里执行构建。

### 构建全部示例

```bash
./build_binary.sh                  # PyInstaller --onefile，全部子目录
./build_binary.sh --test           # …构建后再跑协议冒烟测试
./build_binary.sh --nuitka --test  # 改用 Nuitka
```

### 只构建指定示例

在根目录传子目录名作为参数：

```bash
./build_binary.sh basic-tool
./build_binary.sh credential-tool sampling-summarizer --test
```

…或进入子目录后调用：

```bash
cd sampling-summarizer
../build_binary.sh --test
```

产物位置：`<subdir>/dist/<plugin-name>`（每个插件一个单文件可执行）。

### 手动构建

```bash
cd basic-tool
pip install pyinstaller

# 使用 spec 文件
pyinstaller pyinstaller.spec

# 或命令行
pyinstaller --onefile --name example-text-tool --strip --noupx example_plugin.py
```

## 分发到 Anna

### Local 分发

Local 与 Binary **共用同一套 v2 安装流程**（解压 →
`tools/{tool_id}/v{version}/` → 切 `current` 软链 → `bin/{name}` shim），
Archive 从 Agent 主机本地路径读取，不需要 HTTP 上传。多文件 Archive
（PyInstaller `--onedir` 携带 `.so` / `_internal/`）也完整支持，参见
[`examples/multifile-binary/python-pyinstaller-onedir/`](../multifile-binary/python-pyinstaller-onedir/)。

```bash
cd basic-tool
../build_binary.sh basic-tool
cd dist && tar czf example-text-tool.tar.gz example-text-tool
```

然后在 Anna Admin：
- 分发方式：**Local**
- Local Archive Path：`/abs/path/to/dist/example-text-tool.tar.gz`
- Version：`dev`（或任意字符串）
- 协议：`stdio`

### Binary 分发

1. 在示例子目录中构建二进制（见上）。
2. 打包：`cd <subdir>/dist && tar czf <name>-darwin-arm64.tar.gz <name>`。
3. 上传到 GitHub Releases / S3 / 任意 HTTP 服务。
4. 在 Anna Admin 中配置 Binary URL。

### uv 分发

在 Anna Admin 中：
- 分发方式：**uv**
- 包名：对应子目录 `pyproject.toml` 中声明的 `name`（如
  `example-text-tool`、`weather-tool`、`gmail-tool`、`sampling-summarizer`、`storage-notebook`）。

## 凭据插件示例

[`credential-tool/credential_plugin.py`](credential-tool/credential_plugin.py)
演示如何与 Anna Nexus 的平台统一授权集成。

### 凭据声明

在 Manifest 的 `credentials` 字段中声明所需凭据，命名与平台提供商对齐即可
自动映射：

```python
"credentials": [
    {
        "name": "WEATHER_API_KEY",       # 与平台 credential_mapping 对齐
        "display_name": "API Key",        # UI 展示名称
        "required": True,
        "sensitive": True,                # 加密存储，UI 不回显
    },
]
```

### 凭据读取（三层优先级）

```python
def tool_get_weather(city: str, *, credentials: dict | None = None) -> dict:
    creds = credentials or {}
    # 1. 平台统一凭据 / 插件级凭据（Agent 注入）
    api_key = creds.get("WEATHER_API_KEY")
    # 2. 环境变量回退（本地开发）
    if not api_key:
        api_key = os.environ.get("WEATHER_API_KEY")
```

### 本地开发测试

```bash
cd credential-tool

# 通过环境变量提供凭据
WEATHER_API_KEY=your_key python credential_plugin.py

# 测试 describe（查看凭据声明）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python credential_plugin.py 2>/dev/null

# 测试带凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_weather","arguments":{"city":"Beijing"},"context":{"credentials":{"WEATHER_API_KEY":"test_key"}}},"id":2}' | python credential_plugin.py 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md)

## Google OAuth 插件示例

[`google-oauth-tool/google_oauth_plugin.py`](google-oauth-tool/google_oauth_plugin.py)
演示如何使用平台提供的 **OAuth2 访问令牌**。与 API Key 凭据不同，插件**不**
管理 OAuth 流程 —— Nexus 处理授权、令牌交换和自动刷新。

### 与 API Key 插件的关键区别

从插件开发角度看，代码**完全一致** —— 都是从 `context.credentials` 读取。
唯一区别在命名对齐：

```python
# API Key 插件 —— 自定义服务凭据
"credentials": [{"name": "WEATHER_API_KEY", ...}]

# OAuth 插件 —— 平台提供商凭据（自动注入）
"credentials": [{"name": "GMAIL_ACCESS_TOKEN", ...}]  # 映射到 Google OAuth $access_token
```

### 本地开发测试

```bash
cd google-oauth-tool

# 通过环境变量提供 OAuth 令牌
GMAIL_ACCESS_TOKEN=ya29.xxx python google_oauth_plugin.py

# 测试 describe
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python google_oauth_plugin.py 2>/dev/null

# 测试带 OAuth 凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"list_messages","arguments":{"query":"is:unread","max_results":5},"context":{"credentials":{"GMAIL_ACCESS_TOKEN":"ya29.test_token"}}},"id":2}' | python google_oauth_plugin.py 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.zh-CN.md) 了解完整 OAuth 流程

## 协议交互示例

```bash
cd basic-tool

# 获取工具清单
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null

# 调用 word_count
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"word_count","arguments":{"text":"hello world"}},"id":2}' | python example_plugin.py 2>/dev/null

# 调用 text_transform
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"text_transform","arguments":{"text":"hello","transform":"upper"}},"id":3}' | python example_plugin.py 2>/dev/null

# 健康检查
echo '{"jsonrpc":"2.0","method":"health","id":4}' | python example_plugin.py 2>/dev/null
```

## 添加自己的工具

挑选最接近你需求的示例（basic / credential / OAuth / sampling / storage），复制其
子目录，然后：

1. 在 `MANIFEST["tools"]` 中添加工具定义（name、description、parameters）
2. 实现工具函数
3. 在 `TOOL_DISPATCH` 字典中注册

```python
# 1. 定义
{"name": "my_tool", "description": "...", "parameters": [...]}

# 2. 实现
def tool_my_tool(arg1: str, arg2: int = 10) -> dict:
    return {"result": "..."}

# 3. 注册
TOOL_DISPATCH["my_tool"] = tool_my_tool
```

如果新示例需要自己的发布名，记得同步更新该子目录下 `pyproject.toml`
（`[project] name`、`[project.scripts]`）和 `pyinstaller.spec` 中的产物名。

## 使用 Host LLM Sampling（Executa v2）

[`sampling-summarizer/sampling_summarizer.py`](sampling-summarizer/sampling_summarizer.py)
演示了长期运行的插件如何请 host（Anna）代理完成一次 LLM 推理 —— 插件从不
持有 API key，也不选择模型。

关键要点（示例中已接好线）：

1. **v2 握手。** 实现 `initialize`，以 `protocolVersion: "2.0"` 和
   `client_capabilities: { sampling: {} }` 响应。
2. **Manifest 声明。** 在 `describe` 返回的 manifest 中加上
   `host_capabilities: ["llm.sample"]`，否则 Nexus 会以
   `-32008 not_negotiated` 拒绝。
3. **反向 RPC。** 使用 SDK [`../../sdk/python/executa_sdk/sampling.py`](../../sdk/python/executa_sdk/sampling.py)：

   ```python
   from executa_sdk import SamplingClient

   sampling = SamplingClient(write_frame=write_frame)  # write_frame = 你的 stdout 写入器
   result = await sampling.create_message(
       messages=[{"role": "user", "content": {"type": "text", "text": "请总结…"}}],
       max_tokens=400,
       system_prompt="你是一个简洁的助手。",
       # 不传 model_preferences → host 回退到用户的 preferred_model。
       metadata={"executa_invoke_id": invoke_id},
   )
   ```

4. **用户授权。** 最终用户需在 Anna Admin 为该 Executa 打开 sampling
   开关（写入 `sampling_grant.enabled = true`）。

完整线协议与错误码：[docs/sampling.zh-CN.md](../../docs/sampling.zh-CN.md)。

## 使用 Persistent Storage（Executa v2）

[`storage-notebook/storage_notebook.py`](storage-notebook/storage_notebook.py)
演示了插件如何在不持有任何云存储凭据的情况下持久化按用户/应用维度的
状态，并上传二进制附件 —— bucket、加密、配额、按应用 ACL 全部由 Anna
负责。

关键要点（示例中已接好线）：

1. **v2 握手。** 实现 `initialize`，以 `protocolVersion: "2.0"` 和
   `client_capabilities: { storage: { kv: true, files: true } }` 响应。
2. **Manifest 声明。** 在 manifest 中声明所需的 host capabilities（如
   `aps.kv`、`aps.files`，以及打算使用的 `storage.user` /
   `storage.app` / `storage.tool` 作用域），否则 Nexus 会以
   `-32008 not_negotiated` 拒绝。
3. **反向 RPC。** 使用 SDK
   [`../../sdk/python/executa_sdk/storage.py`](../../sdk/python/executa_sdk/storage.py)
   与 [`files.py`](../../sdk/python/executa_sdk/files.py)：

   ```python
   from executa_sdk import StorageClient, FilesClient, make_response_router

   storage = StorageClient()
   files = FilesClient()
   route_response = make_response_router(storage, files)  # 复用 stdin

   # 乐观并发的 KV 写入
   cur = await storage.get("notes/log")
   await storage.set("notes/log", new_value, if_match=cur.get("etag"))

   # 两步式文件上传（预签名 PUT → finalize）
   info = await files.upload_begin(path="attachments/x.txt", size_bytes=N,
                                   content_type="text/plain")
   # …把字节 PUT 到 info["put_url"]…
   await files.upload_complete(path="attachments/x.txt", etag=etag, size_bytes=N)
   ```

4. **用户授权。** 最终用户需在 Anna Admin 为该 Executa 打开持久化
   存储开关（写入 `storage_grant.scopes = ["user", …]` 及配额覆盖）。

完整线协议、错误码与配额语义：[docs/persistent-storage.zh-CN.md](../../docs/persistent-storage.zh-CN.md)。
