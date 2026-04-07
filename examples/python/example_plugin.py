#!/usr/bin/env python3
"""Executa 插件示例 — Python 实现

这是一个完整的 Executa 插件示例，展示如何实现标准的 JSON-RPC 2.0 over stdio 协议。
Anna Agent 可以自动发现、加载并调用此插件暴露的工具。

运行方式：
    python example_plugin.py

安装为 uv 工具：
    uv tool install .

构建为独立二进制：
    ./build_binary.sh --test

协议要求：
    - stdin:  接收 JSON-RPC 请求（每行一个 JSON 对象）
    - stdout: 返回 JSON-RPC 响应（每行一个 JSON 对象）
    - stderr: 日志输出（不会干扰协议通信）
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# 单条 stdio 消息大小阈值（字节），超过后自动使用文件传输
MAX_STDIO_MESSAGE_BYTES = 512 * 1024


# ─── Manifest（自描述清单） ──────────────────────────────────────────
#
# name:         工具唯一标识符，对应 Anna Admin 的 tool_id
# display_name: 人类可读名称，对应 Anna Admin 的 name
# tools:        工具列表，每个工具包含 name、description、parameters

MANIFEST = {
    "name": "example-text-tool",
    "display_name": "Example Text Tool",
    "version": "1.0.0",
    "description": "一个示例文本处理工具，演示 Executa 插件协议的完整实现",
    "author": "Anna Developer",
    "tools": [
        {
            "name": "word_count",
            "description": "统计文本中的字数、字符数和行数",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "要分析的文本内容",
                    "required": True,
                },
            ],
        },
        {
            "name": "text_transform",
            "description": "对文本进行格式转换（大写、小写、标题、反转）",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "要转换的文本",
                    "required": True,
                },
                {
                    "name": "transform",
                    "type": "string",
                    "description": "转换类型: upper / lower / title / reverse",
                    "required": True,
                },
            ],
        },
        {
            "name": "text_repeat",
            "description": "重复文本指定次数，可选分隔符",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "要重复的文本",
                    "required": True,
                },
                {
                    "name": "count",
                    "type": "integer",
                    "description": "重复次数（1-100）",
                    "required": False,
                    "default": 2,
                },
                {
                    "name": "separator",
                    "type": "string",
                    "description": "分隔符",
                    "required": False,
                    "default": " ",
                },
            ],
        },
        {
            "name": "batch_word_count",
            "description": "批量统计多段文本的字数（演示 array 参数用法）",
            "parameters": [
                {
                    "name": "texts",
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要分析的文本列表",
                    "required": True,
                },
            ],
        },
        {
            "name": "generate_dataset",
            "description": "生成模拟数据集（可产生大型响应，演示文件传输机制）",
            "parameters": [
                {
                    "name": "rows",
                    "type": "integer",
                    "description": "生成的数据行数（1-100000，超过约 5000 行时会触发文件传输）",
                    "required": False,
                    "default": 100,
                },
                {
                    "name": "columns",
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要包含的列名列表，可选: id / name / email / score / timestamp / description",
                    "required": False,
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


import hashlib
import string
import random as _random


def tool_word_count(text: str) -> dict:
    """统计文本的字数、字符数和行数"""
    lines = text.split("\n")
    words = text.split()
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
        "characters_no_spaces": len(text.replace(" ", "")),
    }


def tool_text_transform(text: str, transform: str) -> dict:
    """对文本进行格式转换"""
    transforms = {
        "upper": str.upper,
        "lower": str.lower,
        "title": str.title,
        "reverse": lambda t: t[::-1],
    }
    fn = transforms.get(transform)
    if fn is None:
        return {
            "error": f"Unknown transform: {transform}. "
            f"Available: {', '.join(transforms.keys())}"
        }
    return {"original": text, "transformed": fn(text), "transform": transform}


def tool_text_repeat(text: str, count: int = 2, separator: str = " ") -> dict:
    """重复文本指定次数"""
    count = max(1, min(100, count))  # clamp to 1..100
    result = separator.join([text] * count)
    return {"result": result, "count": count}


def tool_batch_word_count(texts: list) -> dict:
    """批量统计多段文本的字数（演示 array 参数用法）"""
    results = []
    for text in texts:
        words = text.split()
        results.append({"text_preview": text[:50], "words": len(words), "characters": len(text)})
    return {"count": len(results), "results": results}


def _make_fake_name(rng: _random.Random) -> str:
    """生成模拟姓名"""
    first = rng.choice(["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
                        "Grace", "Hank", "Ivy", "Jack", "Karen", "Leo"])
    last = rng.choice(["Smith", "Johnson", "Williams", "Brown", "Jones",
                       "Garcia", "Miller", "Davis", "Wilson", "Taylor"])
    return f"{first} {last}"


def tool_generate_dataset(rows: int = 100, columns: list | None = None) -> dict:
    """生成模拟数据集。行数较大时会产生大型 JSON 响应，自动触发文件传输。"""
    rows = max(1, min(100000, rows))
    available_cols = ["id", "name", "email", "score", "timestamp", "description"]
    if not columns:
        columns = ["id", "name", "email", "score"]
    # 过滤无效列名
    columns = [c for c in columns if c in available_cols] or ["id"]

    rng = _random.Random(42)  # 固定种子保证可复现
    dataset = []
    for i in range(rows):
        row = {}
        for col in columns:
            if col == "id":
                row["id"] = i + 1
            elif col == "name":
                row["name"] = _make_fake_name(rng)
            elif col == "email":
                name = _make_fake_name(rng).lower().replace(" ", ".")
                row["email"] = f"{name}@example.com"
            elif col == "score":
                row["score"] = round(rng.uniform(0, 100), 2)
            elif col == "timestamp":
                ts = 1700000000 + rng.randint(0, 10000000)
                row["timestamp"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            elif col == "description":
                words = rng.choices(string.ascii_lowercase.split() + [
                    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
                    "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
                    "incididunt", "ut", "labore", "et", "dolore", "magna",
                ], k=rng.randint(10, 30))
                row["description"] = " ".join(words)
        dataset.append(row)

    # 估算响应大小
    sample_json = json.dumps(dataset[:1], ensure_ascii=False)
    estimated_bytes = len(sample_json.encode("utf-8")) * rows

    return {
        "rows": rows,
        "columns": columns,
        "estimated_bytes": estimated_bytes,
        "file_transport": estimated_bytes > MAX_STDIO_MESSAGE_BYTES,
        "dataset": dataset,
    }


# ─── 工具分发表 ───────────────────────────────────────────────────

TOOL_DISPATCH = {
    "word_count": tool_word_count,
    "text_transform": tool_text_transform,
    "text_repeat": tool_text_repeat,
    "batch_word_count": tool_batch_word_count,
    "generate_dataset": tool_generate_dataset,
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
    """处理 describe 请求 — 返回工具自描述清单"""
    return make_response(request_id, result=MANIFEST)


def handle_invoke(request_id, params):
    """处理 invoke 请求 — 执行工具调用"""
    tool_name = params.get("tool")
    arguments = params.get("arguments", {})

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
        result = fn(**arguments)
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


# ─── 响应发送（支持文件传输） ───────────────────────────────────


def send_response(response_dict: dict) -> None:
    """发送 JSON-RPC 响应，大型结果自动使用文件传输。

    当序列化后的 JSON 超过 MAX_STDIO_MESSAGE_BYTES 时，将完整响应
    写入临时文件，通过 stdout 只发送一条包含文件路径的轻量指针。
    Agent 读取后会自动删除临时文件。
    """
    payload = json.dumps(response_dict, ensure_ascii=False)
    payload_bytes = payload.encode("utf-8")

    if len(payload_bytes) > MAX_STDIO_MESSAGE_BYTES:
        # 写入临时文件（Agent 读取后自动删除）
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="executa-resp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            os.close(fd)
            raise

        # 通过 stdout 发送文件指针
        pointer = json.dumps({
            "jsonrpc": "2.0",
            "id": response_dict.get("id"),
            "__file_transport": tmp_path,
        })
        print(
            f"📦 Response too large ({len(payload_bytes)} bytes), "
            f"using file transport: {tmp_path}",
            file=sys.stderr,
        )
        sys.stdout.write(pointer + "\n")
    else:
        sys.stdout.write(payload + "\n")

    sys.stdout.flush()


# ─── 主循环（stdio JSON-RPC 服务） ──────────────────────────────


def main():
    """主入口：从 stdin 逐行读取 JSON-RPC 请求，通过 stdout 返回响应。

    重要：所有日志输出到 stderr，避免干扰协议通信。
    """
    print("🔌 Example Executa plugin started", file=sys.stderr)
    print(f"   Tools: {list(TOOL_DISPATCH.keys())}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        print(f"← {line}", file=sys.stderr)

        response_str = handle_request(line)
        response_dict = json.loads(response_str)

        send_response(response_dict)

        print(f"→ (sent, {len(response_str)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
