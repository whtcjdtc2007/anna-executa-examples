# Python Executa 插件示例

## 概述

本目录包含两个完整的 Python Executa 插件示例：

| 示例 | 文件 | 说明 |
|------|------|------|
| **基础插件** | `example_plugin.py` | 文本处理工具集（word_count、text_transform、batch_word_count） |
| **凭据插件** | `credential_plugin.py` | 天气查询工具，演示凭据声明与平台统一授权集成 |

## 运行方式

### 直接运行（开发/调试）

```bash
python example_plugin.py
```

在另一个终端测试：

```bash
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python example_plugin.py 2>/dev/null
```

### 通过 uv 安装（推荐的 Local 分发方式）

```bash
# 安装为全局工具
uv tool install .

# 运行
example-text-tool
```

### 通过 pipx 安装

```bash
pipx install .
example-text-tool
```

## 构建为独立二进制

### PyInstaller（推荐）

```bash
# 一键构建
./build_binary.sh

# 构建并测试
./build_binary.sh --test
```

### Nuitka（更小体积）

```bash
./build_binary.sh --nuitka --test
```

### 手动构建

```bash
pip install pyinstaller

# 使用 spec 文件
pyinstaller example-text-tool.spec

# 或命令行
pyinstaller --onefile --name example-text-tool --strip --noupx example_plugin.py
```

## 分发到 Anna

### Local 分发

在 Anna Admin 中：
- 分发方式：**Local**
- 路径填写 Python 脚本路径，如 `/path/to/example_plugin.py`
- 协议选择：`stdio`

### Binary 分发

1. 构建二进制：`./build_binary.sh`
2. 打包：`cd dist && tar czf example-text-tool-darwin-arm64.tar.gz example-text-tool`
3. 上传到 GitHub Releases / S3 / 任意 HTTP 服务
4. 在 Anna Admin 中配置 Binary URL

### uv 分发

在 Anna Admin 中：
- 分发方式：**uv**
- 包名：`example-text-tool`（或 PyPI 包名）

## 文件说明

| 文件 | 说明 |
|------|------|
| `example_plugin.py` | 基础插件主程序（可直接运行） |
| `credential_plugin.py` | 凭据插件示例（演示平台统一授权集成） |
| `pyproject.toml` | Python 包配置（uv/pipx 安装需要） |
| `build_binary.sh` | 一键构建脚本（PyInstaller / Nuitka） |
| `example-text-tool.spec` | PyInstaller 配置文件 |
| `weather-tool.spec` | 凭据插件的 PyInstaller 配置文件 |

## 凭据插件示例

`credential_plugin.py` 演示如何与 Anna Nexus 的平台统一授权集成：

### 凭据声明

在 Manifest 的 `credentials` 字段中声明所需凭据，命名与平台提供商对齐即可自动映射：

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
# 通过环境变量提供凭据
WEATHER_API_KEY=your_key python credential_plugin.py

# 测试 describe（查看凭据声明）
echo '{"jsonrpc":"2.0","method":"describe","id":1}' | python credential_plugin.py 2>/dev/null

# 测试带凭据的 invoke
echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"get_weather","arguments":{"city":"Beijing"},"context":{"credentials":{"WEATHER_API_KEY":"test_key"}}},"id":2}' | python credential_plugin.py 2>/dev/null
```

> 详见 [平台统一授权文档](../../docs/authorization.md)

## 协议交互示例

```bash
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
