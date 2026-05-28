"""Anna Executa Python SDK — Embeddings support

``EmbeddingsClient`` 让一个长时间运行的 Executa 插件向其 host Agent 发起
反向 JSON-RPC ``embeddings/create``，由 host 完成向量计算并把结果回送。

为什么这样设计：
- Plugin **不需要**自己的 embedding API key —— 凭据 / 计费 / 模型路由
  都由 host (Anna) 持有。
- 对外暴露的是 host-stable 别名（如 ``anna-managed-v1``），后端换模型 /
  升维零感知。配额走独立的每日 token 池，不与对话型 LLM 互相挤占。

线程模型与 :class:`SamplingClient` 完全一致：
- plugin stdin reader 必须把所有"无 method"的帧调度到
  :meth:`EmbeddingsClient.dispatch_response`
- 单个 EmbeddingsClient 实例即可（多路复用所有未决请求）

Wire format 见 ``matrix-nexus/docs/design/app-llm-embeddings.md`` §7。
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

# 复用 sampling 模块里已经实现的帧写出（保持单一来源，避免格式漂移）
from .sampling import _write_frame


# ─── 与 matrix/src/executa/protocol.py 同步 ───────────────────────────

METHOD_EMBEDDINGS_CREATE = "embeddings/create"

EMBED_ERR_NOT_GRANTED = -32501
EMBED_ERR_QUOTA_EXCEEDED = -32502
EMBED_ERR_PROVIDER_ERROR = -32503
EMBED_ERR_INVALID_REQUEST = -32504
EMBED_ERR_TIMEOUT = -32505
EMBED_ERR_MAX_TOKENS_EXCEEDED = -32506
EMBED_ERR_NOT_NEGOTIATED = -32507
EMBED_ERR_USER_DENIED = -32508


class EmbeddingsError(Exception):
    """host 返回的 JSON-RPC error 包装。"""

    def __init__(self, code: int, message: str, data: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


# ─── 内部状态 ────────────────────────────────────────────────────────


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


# ─── 公共 API ─────────────────────────────────────────────────────────


class EmbeddingsClient:
    """向 host 发起反向 ``embeddings/create`` 的客户端。

    用法：

        client = EmbeddingsClient()

        # 在 plugin stdin reader 中：
        async def on_stdin_message(msg):
            if client.is_response_envelope(msg):
                client.dispatch_response(msg)
                return
            # ... 处理普通 invoke 等

        # 在 tool handler 内：
        result = await client.create(
            input=["hello world", "另一段文本"],
            model="anna-managed-v1",
        )
        vectors = [d["embedding"] for d in result["data"]]
    """

    def __init__(self, *, write_frame: Callable[[dict], None] | None = None) -> None:
        self._write_frame = write_frame or _write_frame
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._disabled_reason: Optional[str] = None

    # — 调用 —

    async def create(
        self,
        *,
        input: Union[str, Sequence[str]],
        model: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict:
        """请求一次 embedding。

        Args:
            input:  字符串或字符串列表。空字符串会被 host 拒绝。
            model:  host-stable alias，默认 ``anna-managed-v1``。
            timeout: 客户端墙钟超时（秒）。

        Returns:
            ``{"object":"list","model":<alias>,"data":[{"index","embedding"}],
              "usage":{"prompt_tokens","total_tokens"},"_meta":{...}}``

        Raises:
            EmbeddingsError: host 返回 JSON-RPC error 时
            asyncio.TimeoutError: 超过 ``timeout`` 仍无响应
        """
        if self._disabled_reason:
            raise EmbeddingsError(EMBED_ERR_NOT_NEGOTIATED, self._disabled_reason)

        if isinstance(input, str):
            if not input.strip():
                raise ValueError("input must be non-empty string")
            inputs: List[str] = [input]
        elif isinstance(input, (list, tuple)):
            if not input:
                raise ValueError("input list must be non-empty")
            inputs = list(input)
        else:
            raise TypeError(
                f"input must be str or sequence of str; got {type(input).__name__}"
            )

        loop = asyncio.get_running_loop()
        self._loop = loop
        req_id = uuid.uuid4().hex

        params: Dict[str, Any] = {"input": inputs}
        if model:
            params["model"] = model

        future: asyncio.Future[dict] = loop.create_future()
        with self._lock:
            self._pending[req_id] = _Pending(future=future)

        envelope = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": METHOD_EMBEDDINGS_CREATE,
            "params": params,
        }
        try:
            self._write_frame(envelope)
        except Exception:
            with self._lock:
                self._pending.pop(req_id, None)
            raise

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._pending.pop(req_id, None)
            raise EmbeddingsError(
                EMBED_ERR_TIMEOUT,
                f"embeddings/create timed out after {timeout}s",
            )

    # — 协调 —

    def disable(self, reason: str) -> None:
        """标记 embeddings 不可用（例如 host 未协商 v2 协议）。"""
        self._disabled_reason = reason

    def is_response_envelope(self, msg: dict) -> bool:
        if not isinstance(msg, dict):
            return False
        if "method" in msg:
            return False
        return "id" in msg and msg.get("id") in self._pending

    def dispatch_response(self, msg: dict) -> bool:
        """把 host 回来的响应 future-resolve 掉。返回是否处理。"""
        if not isinstance(msg, dict) or "method" in msg:
            return False
        req_id = msg.get("id")
        if req_id is None:
            return False
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is None:
            return False
        loop = self._loop
        if loop is None or pending.future.done():
            return True

        def _resolve() -> None:
            if pending.future.done():
                return
            err = msg.get("error")
            if err:
                pending.future.set_exception(
                    EmbeddingsError(
                        code=int(err.get("code", -32603)),
                        message=str(err.get("message", "unknown error")),
                        data=err.get("data"),
                    )
                )
            else:
                pending.future.set_result(msg.get("result") or {})

        try:
            loop.call_soon_threadsafe(_resolve)
        except RuntimeError:
            _resolve()
        return True


__all__ = [
    "EmbeddingsClient",
    "EmbeddingsError",
    "METHOD_EMBEDDINGS_CREATE",
    "EMBED_ERR_NOT_GRANTED",
    "EMBED_ERR_QUOTA_EXCEEDED",
    "EMBED_ERR_PROVIDER_ERROR",
    "EMBED_ERR_INVALID_REQUEST",
    "EMBED_ERR_TIMEOUT",
    "EMBED_ERR_MAX_TOKENS_EXCEEDED",
    "EMBED_ERR_NOT_NEGOTIATED",
    "EMBED_ERR_USER_DENIED",
]
