#!/usr/bin/env python3
"""llm_via_executa_plugin.py — minimal Executa that performs an LLM
completion on behalf of the calling app.

The point of this plugin is to *demonstrate* the alternative path the
LLM Demo app exposes in its UI:

    iframe ── anna.tools.invoke ──▶ this Executa ── sampling/createMessage ──▶ host LLM

It is intentionally tiny — one tool, ``complete``, that wraps the host
``sampling/createMessage`` reverse-RPC. Use it side-by-side with the
``anna.llm.complete`` path the same app exercises to compare the two
LLM access surfaces:

* ``anna.llm.complete`` — direct call from iframe → host LLM. Billed
  to the end-user, governed by ``host_api.llm`` grants.
* ``anna.tools.invoke`` → this Executa → ``sampling/createMessage`` —
  indirect, billed to the end-user via the Executa's
  ``sampling_grant``, governed by ``host_capabilities: ["llm.sample"]``.

Mostly mirrors examples/python/sampling-summarizer; the differences:
* one tool (``complete``) instead of one summarizer
* the system prompt + user prompt come from the caller verbatim
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Fallback for fresh checkouts: locate the in-repo SDK when not pip-installed.
try:
    import executa_sdk  # noqa: F401
except ModuleNotFoundError:
    _SDK_PATH = Path(__file__).resolve().parents[4] / "sdk" / "python"
    if _SDK_PATH.is_dir():
        sys.path.insert(0, str(_SDK_PATH))

from executa_sdk import (  # noqa: E402
    PROTOCOL_VERSION_V2,
    AgentSession,
    AgentSessionClient,
    AgentError,
    SamplingClient,
    SamplingError,
)

# ─── Manifest ────────────────────────────────────────────────────────
# The host keys this plugin by the *server-assigned tool_id* (the
# registration key it resolves from the on-disk shim name / nexus
# ``executable_name or tool_id``), NOT by any name declared here. A
# self-declared ``name`` is therefore optional and purely diagnostic;
# we omit it so there is no placeholder to keep in sync with the minted
# tool_id. The app's manifest.json (required_executas + host_api.tools)
# and the on-disk shim name are what bind this Executa to its tool_id.

MANIFEST = {
    "display_name": "LLM via Executa",
    "version": "0.3.0",
    "description": (
        "Performs an LLM completion on behalf of the calling app by "
        "asking the host to sample (sampling/createMessage)."
    ),
    "author": "Anna Developer",
    # Required for v2 reverse sampling. Without this, the host will
    # refuse sampling requests with error -32008 (NOT_NEGOTIATED).
    # ``llm.agent.auto`` is needed for the ``agent_session`` tool, which
    # drives ``agent/session.*`` reverse-RPC: the host checks
    # ``UserExecuta.custom_config.llm_grant.agent.auto`` (granted via the
    # executa Permissions modal) and rejects with -32001 if absent.
    "host_capabilities": ["llm.sample", "llm.agent.auto"],
    "tools": [
        {
            "name": "complete",
            "description": (
                "Run a single-turn LLM completion. The host picks the "
                "model and bills the user's quota. Exposes the full "
                "sampling/createMessage attribute surface (temperature, "
                "stop sequences, model preferences) so you can see how each "
                "maps onto the host LLM call."
            ),
            "parameters": [
                {
                    "name": "prompt",
                    "type": "string",
                    "description": "User prompt to send to the LLM.",
                    "required": True,
                },
                {
                    "name": "system_prompt",
                    "type": "string",
                    "description": "Optional system instruction.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "max_tokens",
                    "type": "integer",
                    "description": "Max output tokens (16-8192).",
                    "required": False,
                    "default": 256,
                },
                {
                    "name": "temperature",
                    "type": "number",
                    "description": (
                        "Sampling temperature 0.0-2.0. Omitted → host uses "
                        "0.7 (it does NOT defer to the provider default)."
                    ),
                    "required": False,
                    "default": None,
                },
                {
                    "name": "stop",
                    "type": "array",
                    "items_type": "string",
                    "description": "Optional stop sequences.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "model_hint",
                    "type": "string",
                    "description": (
                        "Preferred model name hint (MCP modelPreferences "
                        "hint), e.g. 'gpt-4o'. Host may substitute an "
                        "equivalent; omit to use the user's saved model."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "cost_priority",
                    "type": "number",
                    "description": "modelPreferences.costPriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "speed_priority",
                    "type": "number",
                    "description": "modelPreferences.speedPriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "intelligence_priority",
                    "type": "number",
                    "description": "modelPreferences.intelligencePriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
            ],
        },
        {
            "name": "sample_chain",
            "description": (
                "Run N sequential sampling/createMessage calls inside a "
                "SINGLE tool invoke, each step feeding the previous answer "
                "back as context. Demonstrates multi-call sampling within "
                "one invoke: it exercises the per-invoke max_calls / "
                "max_tokens quota AND the host's automatic sampling-token "
                "renewal — a long chain outlives the short-lived token, so "
                "later steps transparently run on a renewed token instead "
                "of failing with 'Signature has expired'. Use 'delay_s' to "
                "stretch the invoke across the token TTL when reproducing "
                "the original instability."
            ),
            "parameters": [
                {
                    "name": "prompt",
                    "type": "string",
                    "description": "Seed prompt for step 1.",
                    "required": True,
                },
                {
                    "name": "steps",
                    "type": "integer",
                    "description": "Number of sequential sampling calls (1-8).",
                    "required": False,
                    "default": 3,
                },
                {
                    "name": "system_prompt",
                    "type": "string",
                    "description": "Optional system instruction applied to every step.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "max_tokens",
                    "type": "integer",
                    "description": "Per-step max output tokens (16-8192).",
                    "required": False,
                    "default": 128,
                },
                {
                    "name": "temperature",
                    "type": "number",
                    "description": "Sampling temperature 0.0-2.0 for every step.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "delay_s",
                    "type": "number",
                    "description": (
                        "Seconds to sleep between steps. Use a large value "
                        "(e.g. > token TTL / steps) to force the invoke to "
                        "outlive the sampling token and exercise renewal."
                    ),
                    "required": False,
                    "default": 0,
                },
                {
                    "name": "model_hint",
                    "type": "string",
                    "description": "Preferred model name hint for every step.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "cost_priority",
                    "type": "number",
                    "description": "modelPreferences.costPriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "speed_priority",
                    "type": "number",
                    "description": "modelPreferences.speedPriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "intelligence_priority",
                    "type": "number",
                    "description": "modelPreferences.intelligencePriority 0.0-1.0.",
                    "required": False,
                    "default": None,
                },
            ],
        },
        {
            "name": "agent_session",
            # Buffered reverse-RPC runs block this invoke for the WHOLE agent
            # loop — the matrix host honors this per-tool deadline when the
            # caller does not negotiate one (default would be 60s, which a
            # single tool-using / image turn can exceed).
            "timeout": 180,
            "description": (
                "Drive an Anna App Session over the Reverse RPC path: the "
                "plugin issues agent/session.* reverse-RPCs to the host. "
                "The 'op' argument selects the operation "
                "(create|run|cancel|history|delete|list). op=run accepts "
                "per-run MCP modelPreferences (model_hint + priorities) and "
                "native image attachments (the session's vision model sees "
                "the images directly \u2014 no analyze_image round-trip). "
                "This mirrors the iframe HOST API anna.agent.session.* "
                "surface so the demo can compare both transports."
            ),
            "parameters": [
                {
                    "name": "op",
                    "type": "string",
                    "description": "Session operation to perform.",
                    "required": True,
                    "enum": [
                        "create",
                        "run",
                        "cancel",
                        "history",
                        "delete",
                        "list",
                    ],
                },
                {
                    "name": "app_session_uuid",
                    "type": "string",
                    "description": (
                        "Target session uuid. Required for "
                        "run/cancel/history/delete; ignored for create/list."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "prompt",
                    "type": "string",
                    "description": "User turn content for op=run.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "attachments",
                    "type": "array",
                    "items_type": "object",
                    "description": (
                        "Native image attachments for op=run (forum #171): "
                        "[{type: 'image/png', url: 'https://\u2026'} | "
                        "{type: 'image/jpeg', data: '<base64 or data: URI>', "
                        "filename?}]. Max 6 images, image/* only; url must "
                        "be public HTTPS. The session's model must be "
                        "vision-capable or the run fails with "
                        "APP_MODEL_NOT_VISION_CAPABLE \u2014 pair with "
                        "model_hint to pick a vision model."
                    ),
                    "required": False,
                    "default": None,
                },
                {
                    "name": "allowed_tools",
                    "type": "array",
                    "items_type": "string",
                    "description": (
                        "Per-run tool subset for op=run \u2014 narrows this "
                        "run's tool surface (and thus the prompt) to the "
                        "listed session-granted tools. Sandbox sessions "
                        "only; inherit-host-tools sessions keep the full "
                        "host kit regardless."
                    ),
                    "required": False,
                    "default": None,
                },
                {
                    "name": "submode",
                    "type": "string",
                    "description": "Agent submode for op=create (auto|fixed).",
                    "required": False,
                    "default": "auto",
                },
                {
                    "name": "system_prompt",
                    "type": "string",
                    "description": (
                        "Optional session-level system prompt for op=create. "
                        "Applies to every run in the session unless a per-run "
                        "systemPrompt overrides it; the platform safety floor "
                        "is always enforced on top."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "run_id",
                    "type": "string",
                    "description": "Run id to cancel for op=cancel.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "model_hint",
                    "type": "string",
                    "description": (
                        "modelPreferences.hints[0].name for op=run — per-run "
                        "soft model preference. Host substring-matches it "
                        "against active models and falls back to the user's "
                        "saved model on miss (never fails the run)."
                    ),
                    "required": False,
                    "default": "",
                },
                {
                    "name": "cost_priority",
                    "type": "number",
                    "description": "modelPreferences.costPriority 0.0-1.0 for op=run.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "speed_priority",
                    "type": "number",
                    "description": "modelPreferences.speedPriority 0.0-1.0 for op=run.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "intelligence_priority",
                    "type": "number",
                    "description": "modelPreferences.intelligencePriority 0.0-1.0 for op=run.",
                    "required": False,
                    "default": None,
                },
                {
                    "name": "include_expired",
                    "type": "boolean",
                    "description": "Include expired sessions for op=list.",
                    "required": False,
                    "default": False,
                },
                {
                    "name": "limit",
                    "type": "integer",
                    "description": "Max sessions to return for op=list (1-100).",
                    "required": False,
                    "default": 50,
                },
            ],
        },
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


# ─── stdio plumbing ──────────────────────────────────────────────────

_stdout_lock = threading.Lock()


def _write_frame(msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False)
    with _stdout_lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


sampling = SamplingClient(write_frame=_write_frame)
# Reverse-RPC client for agent/session.* — shares the same stdout writer
# as SamplingClient so both multiplex over the single stdio channel.
agent_client = AgentSessionClient(write_frame=_write_frame)


# ─── Tool implementation ─────────────────────────────────────────────


def _build_model_preferences(
    *,
    model_hint: str = "",
    cost_priority: float | None = None,
    speed_priority: float | None = None,
    intelligence_priority: float | None = None,
) -> dict | None:
    """Assemble an MCP-style ``modelPreferences`` dict from flat tool args.

    Returns ``None`` when nothing was specified so the host falls back to
    the user's saved ``preferred_model``.
    """
    prefs: dict = {}
    if model_hint and model_hint.strip():
        prefs["hints"] = [{"name": model_hint.strip()}]
    for key, val in (
        ("costPriority", cost_priority),
        ("speedPriority", speed_priority),
        ("intelligencePriority", intelligence_priority),
    ):
        if val is not None:
            prefs[key] = max(0.0, min(1.0, float(val)))
    return prefs or None


def _extract_text(result: dict) -> str:
    content = result.get("content") or {}
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "")
    return ""


async def _complete(
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 256,
    temperature: float | None = None,
    stop: list[str] | None = None,
    model_hint: str = "",
    cost_priority: float | None = None,
    speed_priority: float | None = None,
    intelligence_priority: float | None = None,
    *,
    invoke_id: str,
) -> dict:
    if not prompt or not prompt.strip():
        return {"text": "", "note": "empty prompt"}

    # 8192 = host sampling per-call cap (DEFAULT_SAMPLING_MAX_TOKENS_PER_CALL)
    max_tokens = max(16, min(8192, int(max_tokens)))
    model_preferences = _build_model_preferences(
        model_hint=model_hint,
        cost_priority=cost_priority,
        speed_priority=speed_priority,
        intelligence_priority=intelligence_priority,
    )

    t0 = time.perf_counter()
    result = await sampling.create_message(
        messages=[
            {
                "role": "user",
                "content": {"type": "text", "text": prompt},
            }
        ],
        max_tokens=max_tokens,
        system_prompt=system_prompt or None,
        temperature=temperature,
        stop_sequences=list(stop) if stop else None,
        model_preferences=model_preferences,
        metadata={"executa_invoke_id": invoke_id, "tool": "complete"},
        timeout=60.0,
    )

    return {
        "text": _extract_text(result),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "stopReason": result.get("stopReason"),
        "modelPreferences": model_preferences,
        # Plugin-measured wall time of the sampling/createMessage round-trip
        # only (excludes tools.invoke transport overhead) — compare it with
        # the app-measured end-to-end total to see where time is spent.
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    }


async def _sample_chain(
    prompt: str,
    steps: int = 3,
    system_prompt: str = "",
    max_tokens: int = 128,
    temperature: float | None = None,
    delay_s: float = 0,
    model_hint: str = "",
    cost_priority: float | None = None,
    speed_priority: float | None = None,
    intelligence_priority: float | None = None,
    *,
    invoke_id: str,
) -> dict:
    """Run ``steps`` sequential sampling calls inside ONE invoke.

    Each step asks the LLM to build on the previous answer, so the chain
    issues multiple ``sampling/createMessage`` reverse-RPCs under a single
    ``invoke_id``. This is the scenario that previously broke once the
    cumulative wall-clock crossed the sampling-token TTL: the host now
    renews the token transparently between calls (see
    matrix/src/executa/sampling.py), so every step succeeds. ``delay_s``
    lets you stretch the chain past the TTL to prove renewal works.
    """
    if not prompt or not prompt.strip():
        return {"text": "", "note": "empty prompt", "steps": []}

    steps = max(1, min(8, int(steps)))
    # 8192 = host sampling per-call cap; the 32k per-invoke pool still gates
    # the chain cumulatively (SAMPLING_MAX_TOKENS_EXCEEDED past it).
    max_tokens = max(16, min(8192, int(max_tokens)))
    delay_s = max(0.0, float(delay_s))
    model_preferences = _build_model_preferences(
        model_hint=model_hint,
        cost_priority=cost_priority,
        speed_priority=speed_priority,
        intelligence_priority=intelligence_priority,
    )

    step_results: list[dict] = []
    models_used: list[str] = []
    prompt_tokens = completion_tokens = total_tokens = 0
    current = prompt.strip()
    t0 = time.perf_counter()

    for i in range(steps):
        if i > 0 and delay_s > 0:
            await asyncio.sleep(delay_s)

        turn_prompt = (
            current
            if i == 0
            else f"Continue and refine the following, adding one new idea:\n\n{current}"
        )
        step_t0 = time.perf_counter()
        result = await sampling.create_message(
            messages=[
                {
                    "role": "user",
                    "content": {"type": "text", "text": turn_prompt},
                }
            ],
            max_tokens=max_tokens,
            system_prompt=system_prompt or None,
            temperature=temperature,
            model_preferences=model_preferences,
            metadata={
                "executa_invoke_id": invoke_id,
                "tool": "sample_chain",
                "step": i + 1,
            },
            timeout=90.0,
        )
        text_out = _extract_text(result)
        usage = result.get("usage") or {}
        prompt_tokens += int(usage.get("inputTokens") or 0)
        completion_tokens += int(usage.get("outputTokens") or 0)
        total_tokens += int(usage.get("totalTokens") or 0)
        model = result.get("model")
        if model:
            models_used.append(model)
        step_results.append(
            {
                "step": i + 1,
                "text": text_out,
                "model": model,
                "usage": usage,
                "stopReason": result.get("stopReason"),
                "elapsed_ms": round((time.perf_counter() - step_t0) * 1000),
            }
        )
        if text_out:
            current = text_out

    return {
        "text": current,
        "steps": step_results,
        "stepCount": len(step_results),
        "modelsUsed": models_used,
        "totalUsage": {
            "inputTokens": prompt_tokens,
            "outputTokens": completion_tokens,
            "totalTokens": total_tokens,
        },
        "modelPreferences": model_preferences,
        # Total wall time across all steps INCLUDING the artificial delays
        # (per-step elapsed_ms excludes them — sum those for pure LLM time).
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    }


async def _agent_session(
    op: str,
    *,
    app_session_uuid: str = "",
    prompt: str = "",
    attachments: list | None = None,
    allowed_tools: list | None = None,
    submode: str = "auto",
    system_prompt: str = "",
    run_id: str = "",
    include_expired: bool = False,
    limit: int = 50,
    model_hint: str = "",
    cost_priority: float | None = None,
    speed_priority: float | None = None,
    intelligence_priority: float | None = None,
    invoke_id: str,
) -> dict:
    """Drive agent/session.* over the Reverse RPC path.

    Sessions live host-side in nexus; this plugin holds only the uuid
    that the caller threads back across invokes. ``list`` needs no uuid
    — it enumerates by sampling_token, the robust way to recover
    sessions after a plugin restart.
    """
    op = (op or "").strip().lower()

    if op == "create":
        sess = await agent_client.create(
            agent_submode=submode or "auto",
            label="llm-demo (reverse-rpc)",
            system_prompt=(system_prompt or "").strip() or None,
        )
        return {
            "op": op,
            "app_session_uuid": sess.uuid,
            "kind": sess.kind,
            "submode": sess.agent_submode,
            # Runtime-ACCURATE tool surface (host ≥ 1.1.0-beta.45):
            # ["*"] = inherits host tools; [] = TEXT-ONLY sandbox — the
            # agent cannot read/write local files and any claimed side
            # effects (changed_files…) would be hallucinated.
            "granted_tools": sess.granted_tools,
            "inherit_host_tools": sess.inherit_host_tools,
            "expires_in": sess.expires_in,
        }

    if op == "list":
        sessions = await agent_client.list(
            include_expired=bool(include_expired),
            limit=int(limit),
        )
        return {"op": op, "count": len(sessions), "sessions": sessions}

    if op == "refresh":
        # Re-mint the token + slide the idle window for an EXISTING session by
        # uuid. Identity-scoped on the sampling_token host-side, so it works
        # even if this process restarted and lost its token cache — the
        # robust "resume after losing handles" path, paired with ``list``.
        if not app_session_uuid:
            raise ValueError("op='refresh' requires 'app_session_uuid'")
        res = await agent_client.refresh(app_session_uuid)
        return {"op": op, **(res or {})}

    # The remaining ops act on an existing session handle.
    if not app_session_uuid:
        raise ValueError(f"op={op!r} requires 'app_session_uuid'")
    handle = AgentSession(
        uuid=app_session_uuid,
        expires_in=0,
        kind="agent",
        agent_submode=submode or "auto",
        fixed_client_id=None,
        granted_tools=[],
    )
    handle._client = agent_client

    if op == "run":
        # Per-run MCP modelPreferences — soft preference resolved host-side
        # with the same semantics as sampling/createMessage (hints substring
        # match → user's saved model → default; never fails the run).
        model_preferences = _build_model_preferences(
            model_hint=model_hint,
            cost_priority=cost_priority,
            speed_priority=speed_priority,
            intelligence_priority=intelligence_priority,
        )
        frames: list[dict] = []
        text_chunks: list[str] = []
        t0 = time.perf_counter()
        async for frame in handle.run(
            prompt or "hello",
            attachments=attachments or None,
            allowed_tools=allowed_tools or None,
            recursion_limit=8,
            model_preferences=model_preferences,
        ):
            frames.append(frame)
            # Matrix host normalizes nexus's OpenAI-style chunks into
            # {"event": "delta", "text": ...} for the executa path (plus a
            # synthesized terminal {"event": "final", "text": <full text>}).
            if frame.get("event") == "delta" and frame.get("text"):
                text_chunks.append(frame["text"])
        return {
            "op": op,
            "app_session_uuid": app_session_uuid,
            "text": "".join(text_chunks),
            "frames": frames,
            "modelPreferences": model_preferences,
            "attachment_count": len(attachments or []),
            # Buffered transport: one number is all we can measure — the
            # whole run (queue + agent loop + streaming) as seen from the
            # plugin.
            "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        }

    if op == "cancel":
        res = await handle.cancel(run_id or "")
        return {"op": op, "app_session_uuid": app_session_uuid, **(res or {})}

    if op == "history":
        res = await handle.history()
        return {"op": op, "app_session_uuid": app_session_uuid, **(res or {})}

    if op == "delete":
        res = await handle.delete()
        return {"op": op, "app_session_uuid": app_session_uuid, **(res or {})}

    raise ValueError(f"unknown op={op!r}")


# ─── JSON-RPC dispatch ───────────────────────────────────────────────


def _make_response(req_id, *, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _handle_initialize(req_id, params: dict) -> dict:
    proto = (params or {}).get("protocolVersion") or "1.1"
    if proto != PROTOCOL_VERSION_V2:
        sampling.disable(
            f"host did not negotiate v2 (offered protocolVersion={proto!r}); "
            "sampling/createMessage requires Executa protocol 2.0"
        )
    return _make_response(
        req_id,
        result={
            "protocolVersion": proto if proto in ("1.1", "2.0") else "2.0",
            "serverInfo": {"name": MANIFEST["display_name"], "version": MANIFEST["version"]},
            "client_capabilities": {"sampling": {}} if proto == PROTOCOL_VERSION_V2 else {},
            "capabilities": {},
        },
    )


def _handle_describe(req_id) -> dict:
    return _make_response(req_id, result=MANIFEST)


def _handle_health(req_id) -> dict:
    return _make_response(
        req_id,
        result={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": MANIFEST["version"],
        },
    )


_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()


def _handle_invoke(req_id, params: dict) -> dict:
    tool = params.get("tool")
    args = params.get("arguments") or {}
    invoke_id = params.get("invoke_id") or ""

    if tool == "complete":
        coro = _complete(invoke_id=invoke_id, **args)
    elif tool == "sample_chain":
        coro = _sample_chain(invoke_id=invoke_id, **args)
    elif tool == "agent_session":
        coro = _agent_session(invoke_id=invoke_id, **args)
    else:
        return _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown tool: {tool}"},
        )

    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        data = fut.result(timeout=320.0)
    except SamplingError as e:
        return _make_response(
            req_id,
            error={"code": e.code, "message": e.message, "data": e.data},
        )
    except AgentError as e:
        return _make_response(
            req_id,
            error={"code": e.code, "message": e.message, "data": e.data},
        )
    except (TypeError, ValueError) as e:
        return _make_response(
            req_id,
            error={"code": -32602, "message": f"Invalid params: {e}"},
        )
    except Exception as e:  # noqa: BLE001
        return _make_response(
            req_id,
            error={"code": -32603, "message": f"Tool execution failed: {e}"},
        )
    return _make_response(req_id, result={"success": True, "tool": tool, "data": data})


def _handle_message(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(_make_response(None, error={"code": -32700, "message": "Parse error"}))
        return

    # Reverse-RPC reply from host → resolve a pending sampling future.
    if "method" not in msg:
        if sampling.dispatch_response(msg):
            return
        if agent_client.dispatch_response(msg):
            return
        print(f"⚠️  unmatched response id={msg.get('id')!r}", file=sys.stderr)
        return

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        resp = _handle_initialize(req_id, params)
    elif method == "describe":
        resp = _handle_describe(req_id)
    elif method == "invoke":
        resp = _handle_invoke(req_id, params)
    elif method == "health":
        resp = _handle_health(req_id)
    elif method == "shutdown":
        resp = _make_response(req_id, result={"ok": True})
    else:
        resp = _make_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    if req_id is not None:
        _write_frame(resp)


def main() -> None:
    print("🔌 llm-via-executa plugin started", file=sys.stderr)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="invoke")
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            pool.submit(_handle_message, line)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
