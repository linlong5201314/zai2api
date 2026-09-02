"""OpenAI Responses API (/v1/responses) — Codex CLI compatibility.

Minimal mapping: input (string | message list) -> chat.completions call,
output_text aggregation, SSE `response.output_text.delta` events on stream.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from ..models import ChatMessage, ChatRequest
from .openai import _chat_completion, require_key

router = APIRouter()


def _input_to_messages(body: dict[str, Any]) -> list[ChatMessage]:
    inp = body.get("input")
    messages: list[ChatMessage] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append(ChatMessage(role="system", content=instructions))
    if isinstance(inp, str):
        messages.append(ChatMessage(role="user", content=inp))
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            content = item.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
                messages.append(ChatMessage(role=role, content=text))
            else:
                messages.append(ChatMessage(role=role, content=str(content or "")))
    if not messages:
        messages.append(ChatMessage(role="user", content=""))
    return messages


def _resp_base(model: str, rid: str, status: str = "in_progress") -> dict[str, Any]:
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": [{
            "id": f"msg_{uuid.uuid4().hex[:20]}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [],
        }],
        "usage": None,
    }


@router.post("/v1/responses")
async def responses(body: dict[str, Any], key: str = Depends(require_key)):
    model = body.get("model") or "glm-4.6"
    messages = _input_to_messages(body)
    stream = bool(body.get("stream"))
    chat = ChatRequest(
        model=model,
        messages=messages,
        stream=stream,
        stream_options=body.get("stream_options"),
        tools=body.get("tools"),
        reasoning_effort=body.get("reasoning_effort"),
        web_search=body.get("web_search"),
    )

    if not stream:
        inner = await _chat_completion(chat, key)
        data = json.loads(inner.body.decode())
        msg = (data.get("choices") or [{}])[0].get("message", {})
        rid = f"resp_{uuid.uuid4().hex[:24]}"
        resp = _resp_base(model, rid, "completed")
        parts = []
        if msg.get("reasoning_content"):
            parts.append({"type": "reasoning",
                          "summary": [{"type": "summary_text",
                                       "text": msg["reasoning_content"]}]})
        parts.append({"type": "output_text", "text": msg.get("content") or ""})
        resp["output"][0]["content"] = parts
        resp["usage"] = data.get("usage")
        return JSONResponse(resp)

    rid = f"resp_{uuid.uuid4().hex[:24]}"

    async def gen() -> AsyncIterator[str]:
        def evt(etype: str, payload: dict[str, Any]) -> str:
            payload = {"type": etype, **payload}
            return f"event: {etype}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield evt("response.created", {"response": _resp_base(model, rid)})
        # reuse the chat machinery by wrapping the streaming response iterator
        chat2 = chat.model_copy(update={"stream": True})
        sr = await _chat_completion(chat2, key)
        text = ""
        reasoning = ""
        async for raw in sr.body_iterator:
            if isinstance(raw, bytes):
                raw = raw.decode()
            if not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if "error" in chunk:
                yield evt("response.failed", {"error": chunk["error"]})
                return
            for ch in chunk.get("choices", []):
                delta = ch.get("delta", {})
                if delta.get("reasoning_content"):
                    reasoning += delta["reasoning_content"]
                if delta.get("content"):
                    text += delta["content"]
                    yield evt("response.output_text.delta",
                              {"delta": delta["content"]})
        yield evt("response.completed",
                  {"response": {**_resp_base(model, rid, "completed"),
                                "output": [{
                                    "id": f"msg_{uuid.uuid4().hex[:20]}",
                                    "type": "message", "role": "assistant",
                                    "status": "completed",
                                    "content": [
                                        {"type": "output_text", "text": text}]}]}})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
