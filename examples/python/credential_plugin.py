#!/usr/bin/env python3
"""Executa 凭据插件示例 — 演示如何使用凭据（API Key / Token）

本示例展示如何：
1. 在 Manifest 中声明所需凭据（credentials 字段）
2. 在 invoke 中从 context.credentials 读取凭据
3. 安全地使用凭据调用外部 API
4. 回退到环境变量以支持本地开发

凭据的三层解析优先级：
  1. 平台统一凭据 — 用户在 /settings/authorizations 一次性配置
  2. 插件级凭据   — 用户在单个插件设置中手动填写
  3. 环境变量     — 本地开发时从 os.environ 读取（插件自行实现）

Agent 会将解析后的凭据通过 invoke 请求的 params.context.credentials 注入，
LLM 不会看到凭据内容，也无法在对话中泄露。

运行方式：
    python credential_plugin.py

本地开发（通过环境变量提供凭据）：
    WEATHER_API_KEY=your_key python credential_plugin.py

协议要求：
    - stdin:  接收 JSON-RPC 请求（每行一个 JSON 对象）
    - stdout: 返回 JSON-RPC 响应（每行一个 JSON 对象）
    - stderr: 日志输出（不会干扰协议通信）
"""

import json
import os
import sys
from datetime import datetime, timezone


# ─── Manifest（自描述清单） ──────────────────────────────────────────
#
# credentials: 声明本插件所需的凭据列表
#   - name:         凭据标识符（传入时的 key，如 WEATHER_API_KEY）
#   - display_name: 人类可读名称（UI 展示用）
#   - description:  用途说明（帮助用户理解该凭据的作用）
#   - required:     是否必须配置（缺失时工具可能无法正常工作）
#   - sensitive:    是否敏感数据（true 时 Nexus 会加密存储，UI 不回显）

MANIFEST = {
    "name": "weather-tool",
    "display_name": "Weather Tool",
    "version": "1.0.0",
    "description": "天气查询工具，演示凭据（API Key）的声明与使用",
    "author": "Anna Developer",
    # ─── 凭据声明 ───────────────────────────────────────────────
    # credentials[].name 是凭据的唯一标识符，Agent 会以此为 key 注入值。
    #
    # 命名最佳实践：
    #   - 使用全大写蛇形命名（如 WEATHER_API_KEY）
    #   - 与平台提供商的 credential_mapping 对齐，实现自动映射
    #     例如：TWITTER_API_KEY、GITHUB_TOKEN、GOOGLE_ACCESS_TOKEN
    #   - 自定义服务用 SERVICE_NAME + 字段类型 命名
    #
    # sensitive=True 的凭据会在 UI 中以密码框显示，不回显明文。
    "credentials": [
        {
            "name": "WEATHER_API_KEY",
            "display_name": "OpenWeatherMap API Key",
            "description": "从 https://openweathermap.org/api 获取的 API Key",
            "required": True,
            "sensitive": True,
        },
        {
            "name": "WEATHER_UNITS",
            "display_name": "温度单位",
            "description": "温度单位偏好: metric（摄氏）/ imperial（华氏）/ standard（开尔文）",
            "required": False,
            "sensitive": False,
            "default": "metric",
        },
    ],
    "tools": [
        {
            "name": "get_weather",
            "description": "查询指定城市的当前天气",
            "parameters": [
                {
                    "name": "city",
                    "type": "string",
                    "description": "城市名称（英文），如 Beijing, Tokyo, London",
                    "required": True,
                },
            ],
        },
        {
            "name": "get_forecast",
            "description": "查询指定城市的未来天气预报",
            "parameters": [
                {
                    "name": "city",
                    "type": "string",
                    "description": "城市名称（英文）",
                    "required": True,
                },
                {
                    "name": "days",
                    "type": "integer",
                    "description": "预报天数（1-5）",
                    "required": False,
                    "default": 3,
                },
            ],
        },
    ],
    "runtime": {
        "type": "uv",
        "min_version": "0.1.0",
    },
}


# ─── 工具实现 ─────────────────────────────────────────────────────


def tool_get_weather(city: str, *, credentials: dict | None = None) -> dict:
    """查询指定城市的当前天气

    凭据获取优先级：
    1. context.credentials（平台统一授权 / 插件级凭据，由 Agent 注入）
    2. 环境变量（本地开发回退）

    在实际实现中，这里会使用 credentials 中的 API Key 调用外部天气 API。
    本示例返回模拟数据以演示凭据注入流程。
    """
    creds = credentials or {}

    # 最佳实践：优先从 context.credentials 读取，回退到环境变量
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    units = creds.get("WEATHER_UNITS") or os.environ.get("WEATHER_UNITS", "metric")

    if not api_key:
        return {
            "error": "WEATHER_API_KEY not configured",
            "hint": (
                "配置方式（任选其一）:\n"
                "  1. 平台统一授权: /settings/authorizations 页面配置\n"
                "  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置\n"
                "  3. 本地开发: WEATHER_API_KEY=xxx python credential_plugin.py"
            ),
        }

    # ─── 实际调用示例（注释） ───
    # import urllib.request
    # url = (
    #     f"https://api.openweathermap.org/data/2.5/weather"
    #     f"?q={city}&appid={api_key}&units={units}"
    # )
    # resp = urllib.request.urlopen(url)
    # data = json.loads(resp.read())
    # ────────────────────────────

    # 模拟数据（演示用）
    unit_symbol = {"metric": "°C", "imperial": "°F", "standard": "K"}.get(units, "°C")
    return {
        "city": city,
        "temperature": f"22{unit_symbol}",
        "humidity": "65%",
        "description": "partly cloudy",
        "wind_speed": "3.5 m/s",
        "api_key_configured": True,
        "api_key_preview": f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***",
        "units": units,
        "_note": "This is simulated data for demonstration purposes",
    }


def tool_get_forecast(
    city: str, days: int = 3, *, credentials: dict | None = None
) -> dict:
    """查询指定城市的天气预报"""
    creds = credentials or {}
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    units = creds.get("WEATHER_UNITS") or os.environ.get("WEATHER_UNITS", "metric")

    if not api_key:
        return {
            "error": "WEATHER_API_KEY not configured",
            "hint": (
                "配置方式（任选其一）:\n"
                "  1. 平台统一授权: /settings/authorizations 页面配置\n"
                "  2. 插件级凭据: Anna Admin → 插件设置 → 凭据配置\n"
                "  3. 本地开发: WEATHER_API_KEY=xxx python credential_plugin.py"
            ),
        }

    days = max(1, min(5, days))
    unit_symbol = {"metric": "°C", "imperial": "°F", "standard": "K"}.get(units, "°C")

    # 模拟预报数据
    forecast = []
    for i in range(days):
        temp = 20 + i * 2
        forecast.append({
            "day": i + 1,
            "temperature_high": f"{temp + 5}{unit_symbol}",
            "temperature_low": f"{temp - 3}{unit_symbol}",
            "description": ["sunny", "cloudy", "rain", "thunderstorm", "clear"][i % 5],
        })

    return {
        "city": city,
        "days": days,
        "forecast": forecast,
        "api_key_configured": True,
        "_note": "This is simulated data for demonstration purposes",
    }


# ─── 工具分发表 ───────────────────────────────────────────────────

TOOL_DISPATCH = {
    "get_weather": tool_get_weather,
    "get_forecast": tool_get_forecast,
}


# ─── JSON-RPC 处理 ───────────────────────────────────────────────


def make_response(id, result=None, error=None):
    """构造 JSON-RPC 2.0 响应"""
    resp = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def handle_describe(request_id):
    """处理 describe 请求 — 返回工具自描述清单（含 credentials 声明）"""
    return make_response(request_id, result=MANIFEST)


def handle_invoke(request_id, params):
    """处理 invoke 请求 — 执行工具调用

    凭据通过 params.context.credentials 注入，格式：
    {
        "tool": "get_weather",
        "arguments": {"city": "Beijing"},
        "context": {
            "credentials": {
                "WEATHER_API_KEY": "ak_xxxxx",
                "WEATHER_UNITS": "metric"
            }
        }
    }

    注意：凭据由 Agent 从 Nexus 获取并解密后注入，
    LLM 不会看到凭据内容，插件也不需要自行管理凭据存储。
    """
    tool_name = params.get("tool")
    arguments = params.get("arguments", {})

    # 从 context 中提取凭据（Agent 注入）
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
        # 将凭据作为 keyword argument 传入工具函数
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
    """处理 health 请求 — 健康检查"""
    return make_response(
        request_id,
        result={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": MANIFEST["version"],
            "tools_count": len(MANIFEST["tools"]),
            "credentials_declared": len(MANIFEST.get("credentials", [])),
        },
    )


def handle_request(line: str) -> str:
    """解析并处理单条 JSON-RPC 请求"""
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


# ─── 主循环（stdio JSON-RPC 服务） ──────────────────────────────


def main():
    """主入口：从 stdin 逐行读取 JSON-RPC 请求，通过 stdout 返回响应。

    重要：所有日志输出到 stderr，避免干扰协议通信。
    """
    print("🔌 Weather credential plugin started", file=sys.stderr)
    print(f"   Tools: {list(TOOL_DISPATCH.keys())}", file=sys.stderr)
    print(
        f"   Credentials required: "
        f"{[c['name'] for c in MANIFEST.get('credentials', [])]}",
        file=sys.stderr,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        print(f"← {line}", file=sys.stderr)

        response = handle_request(line)

        # 响应通过 stdout — 必须 flush，避免缓冲阻塞
        print(response, flush=True)

        print(f"→ {response}", file=sys.stderr)


if __name__ == "__main__":
    main()
