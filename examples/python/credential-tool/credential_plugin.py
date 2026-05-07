#!/usr/bin/env python3
"""Executa Credential Plugin Example — Demonstrates how to use credentials (API Key / Token)

This example shows how to:
1. Declare required credentials in the Manifest (credentials field)
2. Read credentials from context.credentials in invoke
3. Securely use credentials to call external APIs
4. Fall back to environment variables for local development

Credential resolution priority (three tiers):
  1. Platform unified credentials — configured once at /settings/authorizations
  2. Plugin-level credentials — manually entered in per-plugin settings
  3. Environment variables — read from os.environ for local development (plugin implements)

The Agent injects resolved credentials via invoke request's params.context.credentials.
LLM cannot see credential values and cannot leak them in conversations.

Usage:
    python credential_plugin.py

Local development (provide credentials via environment variables):
    WEATHER_API_KEY=your_key python credential_plugin.py

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
# credentials: Declares the list of credentials required by this plugin
#   - name:         Credential identifier (the key used when passed in, e.g. WEATHER_API_KEY)
#   - display_name: Human-readable name (displayed in the UI)
#   - description:  Usage description (helps users understand the credential's purpose)
#   - required:     Whether configuration is required (tool may not work properly if missing)
#   - sensitive:    Whether it is sensitive data (when true, Nexus encrypts storage; UI does not echo)

MANIFEST = {
    "name": "weather-tool",
    "display_name": "Weather Tool",
    "version": "1.0.0",
    "description": "Weather query tool demonstrating credential (API Key) declaration and usage",
    "author": "Anna Developer",
    # ─── Credential Declaration ───────────────────────────────────────
    # credentials[].name is the unique identifier; Agent uses it as the key for injection.
    #
    # Naming best practices:
    #   - Use UPPER_SNAKE_CASE (e.g. WEATHER_API_KEY)
    #   - Align with platform provider's credential_mapping for automatic mapping
    #     e.g.: TWITTER_ACCESS_TOKEN, GITHUB_TOKEN, GOOGLE_ACCESS_TOKEN
    #   - Custom services: SERVICE_NAME + field type
    #
    # sensitive=True credentials are displayed as password fields in UI, not echoed.
    "credentials": [
        {
            "name": "WEATHER_API_KEY",
            "display_name": "OpenWeatherMap API Key",
            "description": "API Key obtained from https://openweathermap.org/api",
            "required": True,
            "sensitive": True,
        },
        {
            "name": "WEATHER_UNITS",
            "display_name": "Temperature Units",
            "description": "Temperature unit preference: metric (Celsius) / imperial (Fahrenheit) / standard (Kelvin)",
            "required": False,
            "sensitive": False,
            "default": "metric",
        },
    ],
    "tools": [
        {
            "name": "get_weather",
            "description": "Query current weather for a specified city",
            "parameters": [
                {
                    "name": "city",
                    "type": "string",
                    "description": "City name (in English), e.g. Beijing, Tokyo, London",
                    "required": True,
                },
            ],
        },
        {
            "name": "get_forecast",
            "description": "Query weather forecast for a specified city",
            "parameters": [
                {
                    "name": "city",
                    "type": "string",
                    "description": "City name (in English)",
                    "required": True,
                },
                {
                    "name": "days",
                    "type": "integer",
                    "description": "Number of forecast days (1-5)",
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


# ─── Tool Implementation ─────────────────────────────────────────────


def tool_get_weather(city: str, *, credentials: dict | None = None) -> dict:
    """Query current weather for a specified city

    Credential resolution priority:
    1. context.credentials (platform authorization / plugin-level, Agent-injected)
    2. Environment variables (local development fallback)

    In a real implementation, this would use the API Key from credentials to call an external weather API.
    This example returns simulated data to demonstrate the credential injection flow.
    """
    creds = credentials or {}

    # Best practice: read from context.credentials first, fall back to env vars
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    units = creds.get("WEATHER_UNITS") or os.environ.get("WEATHER_UNITS", "metric")

    if not api_key:
        return {
            "error": "WEATHER_API_KEY not configured",
            "hint": (
                "Configuration options (choose one):\n"
                "  1. Platform authorization: /settings/authorizations page\n"
                "  2. Plugin-level credentials: Anna Admin → Plugin Settings → Credentials\n"
                "  3. Local development: WEATHER_API_KEY=xxx python credential_plugin.py"
            ),
        }

    # ─── Actual API call example (commented out) ───
    # import urllib.request
    # url = (
    #     f"https://api.openweathermap.org/data/2.5/weather"
    #     f"?q={city}&appid={api_key}&units={units}"
    # )
    # resp = urllib.request.urlopen(url)
    # data = json.loads(resp.read())
    # ────────────────────────────

    # Simulated data (for demonstration)
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
    """Query weather forecast for a specified city"""
    creds = credentials or {}
    api_key = creds.get("WEATHER_API_KEY") or os.environ.get("WEATHER_API_KEY")
    units = creds.get("WEATHER_UNITS") or os.environ.get("WEATHER_UNITS", "metric")

    if not api_key:
        return {
            "error": "WEATHER_API_KEY not configured",
            "hint": (
                "Configuration options (choose one):\n"
                "  1. Platform authorization: /settings/authorizations page\n"
                "  2. Plugin-level credentials: Anna Admin → Plugin Settings → Credentials\n"
                "  3. Local development: WEATHER_API_KEY=xxx python credential_plugin.py"
            ),
        }

    days = max(1, min(5, days))
    unit_symbol = {"metric": "°C", "imperial": "°F", "standard": "K"}.get(units, "°C")

    # Simulated forecast data
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


# ─── Tool Dispatch Table ─────────────────────────────────────────────

TOOL_DISPATCH = {
    "get_weather": tool_get_weather,
    "get_forecast": tool_get_forecast,
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
    """Handle describe request — return the tool manifest (including credentials declaration)"""
    return make_response(request_id, result=MANIFEST)


def handle_invoke(request_id, params):
    """Handle invoke request — execute a tool call

    Credentials are injected via params.context.credentials, format:
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

    Note: Credentials are fetched and decrypted by the Agent from Nexus before injection.
    The LLM cannot see credential values, and the plugin does not need to manage credential storage.
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
        # Pass credentials as a keyword argument to the tool function
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
    """Handle health request — health check"""
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
    """Main entry point: reads JSON-RPC requests line by line from stdin, returns responses via stdout.

    Important: All log output goes to stderr to avoid interfering with protocol communication.
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

        # Response via stdout — must flush to avoid buffer blocking
        print(response, flush=True)

        print(f"→ {response}", file=sys.stderr)


if __name__ == "__main__":
    main()
