"""OpenAI-compatible endpoints: /v1/chat/completions, /v1/models, /v1/responses."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import config
from ..converter import StreamAccumulator, build_upstream_messages, jwt_payload
from ..models import (ChatRequest, ModelVariant, content_chunk, estimate_tokens,
                      final_response, normalize_usage, public_model_list,
                      usage_obj)
from ..token_pool import (NoCredentialError, UpstreamAuthError, UpstreamWafError,
                          pool)
from ..tools_emulator import inject_tools, parse_tool_calls, tool_names
from ..zai_client import client as zai_client
from ..db import database

log = logging.getLogger("zai2api.api")
router = APIRouter()


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

async def require_key(authorization: str = Header(default="")) -> str:
    if not config.AUTH_TOKENS:
        return "anonymous"
    key = authorization.replace("Bearer", "").strip() if authorization else ""
    if key not in config.AUTH_TOKENS:
        raise HTTPException(status_code=401,
                            detail={"error": {"message": "Invalid API key",
                                              "type": "invalid_request_error"}})
    return key[:8] + "…"


# ---------------------------------------------------------------------------
# shared chat machinery — yields internal events
# ---------------------------------------------------------------------------

async def run_chat(req: ChatRequest, key_hint: str) -> AsyncIterator[tuple[str, Any]]:
    """Run one chat completion against upstream with credential rotation.

    Yields ("reasoning", str) | ("content", str) | ("done", info) | ("error", exc)
    Retries with a fresh credential only while nothing has been emitted.
    """
    variant = ModelVariant(req.model)
    messages = build_upstream_messages(req.messages)
    tools = req.tools or []
    if tools:
        messages = inject_tools(messages, tools)
    web_search = bool(req.web_search)

    info: dict[str, Any] = {"model": req.model, "key_hint": key_hint,
                            "ttft_ms": None, "usage": None,
                            "token_kind": None, "http_status": None,
                            "error": None}
    emitted = False
    last_error: Exception | None = None

    for attempt in range(max(1, config.RETRY_COUNT)):
        try:
            cred = await pool.acquire(need_account=variant.needs_account)
        except NoCredentialError as e:
            yield "error", e
            info["error"] = str(e)
            return

        info["token_kind"] = cred["kind"]
        user_id = (jwt_payload(cred["token"]) or {}).get("id", "")
        payload = zai_client.build_v2_payload(
            messages, variant.upstream()["model"],
            thinking=variant.effective_thinking(),
            web_search=web_search)
        start = time.perf_counter()
        acc = StreamAccumulator(req.model)
        stream_error: Any = None

        try:
            async for ev in zai_client.chat_stream(cred["token"], payload, user_id):
                if ev.error:
                    stream_error = ev.error
                    break
                for chunk in acc.feed(ev):
                    if not emitted:
                        emitted = True
                        info["ttft_ms"] = round(
                            (time.perf_counter() - start) * 1000, 1)
                    delta = chunk["choices"][0]["delta"]
                    if "reasoning_content" in delta:
                        yield "reasoning", delta["reasoning_content"]
                    elif "content" in delta:
                        yield "content", delta["content"]
                if ev.usage:
                    info["usage"] = normalize_usage(ev.usage)
            if stream_error is not None:
                # upstream reports captcha/verify failures inside the stream
                code = ""
                if isinstance(stream_error, dict):
                    code = str(stream_error.get("code")
                               or stream_error.get("error_code") or "")
                if "CAPTCHA" in code:
                    raise UpstreamWafError(f"captcha required: {stream_error}")
                raise RuntimeError(f"upstream event error: {stream_error}")
            await pool.report_ok(cred["token"])
            info["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
            info["reasoning_text"] = acc.reasoning_text
            info["answer_text"] = acc.answer_text
            if not emitted:
                raise RuntimeError("upstream returned an empty stream")
            yield "done", info
            return
        except UpstreamAuthError as e:
            await pool.report_auth_fail(cred["token"], cred["kind"])
            last_error = e
            info["http_status"] = 401
        except UpstreamWafError as e:
            await pool.report_server_error(cred["token"])
            last_error = e
            info["http_status"] = 405
        except (httpx.HTTPError, RuntimeError) as e:
            if emitted:
                info["error"] = str(e)
                yield "error", e
                return
            await pool.report_server_error(cred["token"])
            last_error = e
        # retry with next credential (only reached when nothing was emitted)

    info["error"] = str(last_error)
    yield "error", last_error or RuntimeError("all credentials exhausted")


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.get("/v1/models")
async def list_models(_key: str = Depends(require_key)):
    return {"object": "list", "data": public_model_list()}


def _sse(data: dict[str, Any] | str) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _chat_completion(req: ChatRequest, key_hint: str):
    variant = ModelVariant(req.model)
    model_name = req.model
    buffered = bool(req.tools)  # emulate tool calls: buffer, parse at end
    include_usage = bool((req.stream_options or {}).get("include_usage"))

    async def stream_gen() -> AsyncIterator[str]:
        content_buf = ""
        reasoning_buf = ""
        start = time.perf_counter()
        info: dict[str, Any] | None = None
        err: Exception | None = None

        yield _sse(content_chunk(model_name, role="assistant"))

        async for kind, data in run_chat(req, key_hint):
            if kind == "error":
                err = data
                break
            elif kind == "reasoning":
                reasoning_buf += data
                if config.THINK_TAGS_MODE == "reasoning":
                    yield _sse(content_chunk(model_name, reasoning=data))
                elif config.THINK_TAGS_MODE == "think":
                    yield _sse(content_chunk(model_name, content=data))
            elif kind == "content":
                content_buf += data
                if not buffered and config.THINK_TAGS_MODE != "think":
                    yield _sse(content_chunk(model_name, content=data))
            elif kind == "done":
                info = data

        if err is not None:
            msg = {"error": {"message": str(err),
                             "type": "upstream_error",
                             "code": "upstream_error"}}
            yield _sse(msg)
            yield _sse("[DONE]")
            _log_request(req, key_hint, info, error=str(err), start=start)
            return

        # thinking <think> mode: emit wrapped once we have the full text
        if config.THINK_TAGS_MODE == "think" and reasoning_buf:
            full = f"<think>{reasoning_buf}</think>{content_buf}"
            yield _sse(content_chunk(model_name, content=full))

        tool_calls = None
        if buffered and req.tools:
            names = tool_names(req.tools)
            tool_calls = parse_tool_calls(content_buf, names)
            if tool_calls:
                tc_delta = [{"index": i, "id": tc["id"],
                             "type": "function",
                             "function": {"name": tc["function"]["name"],
                                          "arguments": tc["function"]["arguments"]}}
                            for i, tc in enumerate(tool_calls)]
                yield _sse(content_chunk(model_name, tool_calls=tc_delta,
                                         finish_reason="tool_calls"))
            else:
                yield _sse(content_chunk(model_name, content=content_buf,
                                         finish_reason="stop"))
        elif not buffered:
            yield _sse(content_chunk(model_name, finish_reason="stop"))

        if include_usage:
            u = (info or {}).get("usage") or _estimate_usage(req, reasoning_buf,
                                                             content_buf)
            uc = {"id": (info or {}).get("id"), "object": "chat.completion.chunk",
                  "created": int(time.time()), "model": model_name,
                  "choices": [], "usage": u}
            yield _sse(uc)
        yield _sse("[DONE]")
        _log_request(req, key_hint, info, start=start,
                     prompt_t=(info or {}).get("usage", {}).get("prompt_tokens"),
                     completion_t=(info or {}).get("usage", {}).get("completion_tokens"))

    async def nonstream() -> dict[str, Any]:
        content_buf = ""
        reasoning_buf = ""
        info = None
        err = None
        async for kind, data in run_chat(req, key_hint):
            if kind == "error":
                err = data
            elif kind == "reasoning":
                reasoning_buf += data
            elif kind == "content":
                content_buf += data
            elif kind == "done":
                info = data
        if err is not None and not content_buf:
            raise HTTPException(status_code=502, detail=str(err))
        usage = (info or {}).get("usage") or _estimate_usage(req, reasoning_buf,
                                                             content_buf)
        if buffered and req.tools:
            calls = parse_tool_calls(content_buf, tool_names(req.tools))
            if calls:
                return final_response(model_name, content_buf,
                                      reasoning=reasoning_buf or None,
                                      tool_calls=calls, usage=usage)
        return final_response(model_name, content_buf,
                              reasoning=reasoning_buf or None, usage=usage)

    if req.stream:
        return StreamingResponse(stream_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no",
                                          "Connection": "keep-alive"})
    return JSONResponse(await nonstream())


def _estimate_usage(req: ChatRequest, reasoning: str, content: str) -> dict[str, int]:
    prompt = sum(estimate_tokens(m.content or "") if isinstance(m.content, str)
                 else estimate_tokens(str(m.content)) for m in req.messages)
    completion = estimate_tokens(reasoning) + estimate_tokens(content)
    return usage_obj(prompt, completion)


def _log_request(req: ChatRequest, key_hint: str, info: dict[str, Any] | None,
                 start: float = 0, error: str | None = None,
                 prompt_t: int | None = None, completion_t: int | None = None):
    info = info or {}
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(database.log_request(
            ts=time.time(), model=req.model, key_hint=key_hint,
            token_kind=info.get("token_kind"),
            status="error" if error else "ok",
            http_status=info.get("http_status") or (500 if error else 200),
            ttft_ms=info.get("ttft_ms"),
            latency_ms=info.get("latency_ms") or round(
                (time.perf_counter() - start) * 1000, 1),
            prompt_t=prompt_t, completion_t=completion_t, error=error,
            curl=f"curl -s {config.HOST}:{config.PORT}/v1/chat/completions "
                 f"-H 'Content-Type: application/json' -d '{{\"model\": "
                 f"\"{req.model}\"}}'"))
    except Exception:  # noqa: BLE001
        pass


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, key: str = Depends(require_key)):
    if not req.messages:
        raise HTTPException(400, "messages is required")
    return await _chat_completion(req, key)
