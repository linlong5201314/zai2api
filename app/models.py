"""OpenAI-compatible request/response schemas + Z.ai model mapping."""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Model registry — synced from live GET /api/models (chat.z.ai, 2026-09)
# public name (lowercase) -> upstream id used by the v2 chat completions API
# ---------------------------------------------------------------------------
MODEL_MAP: dict[str, dict[str, str]] = {
    "glm-4.5": {"id": "0727-360B-API", "name": "GLM-4.5"},
    "glm-4.5-air": {"id": "0727-106B-API", "name": "GLM-4.5-Air"},
    "glm-4-flash": {"id": "glm-4-flash", "name": "GLM-4-Flash"},
    "glm-4-air": {"id": "glm-4-air-250414", "name": "GLM-4-32B"},
    "glm-4.1v": {"id": "GLM-4.1V-Thinking-FlashX", "name": "GLM-4.1V-9B-Thinking"},
    "glm-4.6v": {"id": "glm-4.6v", "name": "GLM-4.6V"},
    "glm-4.7": {"id": "glm-4.7", "name": "GLM-4.7"},
    "glm-5.2": {"id": "glm-5.2", "name": "GLM-5.2"},
    "glm-5.3": {"id": "glm-5.3", "name": "GLM-5.3"},
    "glm-5.3-flash": {"id": "x-preview-l", "name": "GLM-5.3-Flash"},
    "glm-5-turbo": {"id": "GLM-5-Turbo", "name": "GLM-5-Turbo"},
    "glm-5v-turbo": {"id": "GLM-5v-Turbo", "name": "GLM-5V-Turbo"},
    "deep-research": {"id": "deep-research", "name": "Z1-Rumination"},
    "zero": {"id": "zero", "name": "Z1-32B"},
}

# models that need an account JWT (guest tier is limited to glm-4.7-class text)
ACCOUNT_REQUIRED_MODELS = {"glm-4.5-air", "glm-4.1v", "glm-4.6v", "glm-5.2",
                           "glm-5.3", "glm-5.3-flash", "glm-5-turbo",
                           "glm-5v-turbo", "deep-research", "zero"}

# models where thinking is enabled by default upstream (meta.free_think)
THINKING_DEFAULT_ON = {"glm-4.7", "glm-5.2", "glm-5.3", "glm-5-turbo",
                       "glm-5v-turbo"}

# models accepting image input
VISION_MODELS = {"glm-4.1v", "glm-4.6v", "glm-5v-turbo"}

SUFFIXES = ("-thinking", "-search", "-nothinking")


class ModelVariant:
    """Parsed public model name -> (base name, thinking, search)."""

    def __init__(self, requested: str):
        name = (requested or "").strip().lower()
        self.requested = name
        self.thinking: bool | None = None  # None = model default
        self.search = False
        changed = True
        while changed:
            changed = False
            for suf in SUFFIXES:
                if name.endswith(suf):
                    name = name[: -len(suf)]
                    changed = True
                    if suf == "-thinking":
                        self.thinking = True
                    elif suf == "-nothinking":
                        self.thinking = False
                    elif suf == "-search":
                        self.search = True
        self.base = name if name in MODEL_MAP else None

    @property
    def known(self) -> bool:
        return self.base is not None

    @property
    def needs_account(self) -> bool:
        return self.known and self.base in ACCOUNT_REQUIRED_MODELS

    @property
    def vision(self) -> bool:
        return self.known and self.base in VISION_MODELS

    def upstream(self) -> dict[str, str]:
        """Return {'model', 'id', 'name'} for the upstream payload."""
        if self.base and self.base in MODEL_MAP:
            m = MODEL_MAP[self.base]
            return {"model": m["id"], "id": m["id"], "name": m["name"]}
        # unknown model: pass through as-is (name shown uppercase)
        return {"model": self.requested, "id": self.requested,
                "name": self.requested.upper()}

    def effective_thinking(self) -> bool:
        if self.thinking is not None:
            return self.thinking
        return self.base in THINKING_DEFAULT_ON if self.base else False


def public_model_list() -> list[dict[str, Any]]:
    """GET /v1/models payload with suffix variants."""
    models = []
    seen: set[str] = set()
    for base in MODEL_MAP:
        variants = [base]
        if base in THINKING_DEFAULT_ON:
            variants += [base + "-thinking", base + "-nothinking", base + "-search"]
        else:
            variants += [base + "-thinking", base + "-search"]
        for v in variants:
            if v in seen:
                continue
            seen.add(v)
            models.append({
                "id": v,
                "object": "model",
                "created": 1750000000,
                "owned_by": "zai2api",
            })
    return models


# ---------------------------------------------------------------------------
# OpenAI request schemas
# ---------------------------------------------------------------------------

class ImageURL(BaseModel):
    url: str
    detail: Optional[str] = None


class ContentPart(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[ImageURL] = None


class FunctionDef(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


class ToolDef(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDef


class ToolChoiceFunc(BaseModel):
    name: str


class ToolChoice(BaseModel):
    mode: Optional[str] = None
    type: Optional[str] = None
    function: Optional[ToolChoiceFunc] = None


class ChatMessage(BaseModel):
    role: str
    content: Optional[str | list[ContentPart]] = None
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    stream_options: Optional[dict[str, Any]] = None
    tools: Optional[list[ToolDef]] = None
    tool_choice: Optional[ToolChoice | str] = None
    reasoning_effort: Optional[str] = None
    stop: Optional[str | list[str]] = None
    user: Optional[str] = None
    # zai2api extensions
    web_search: Optional[bool] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# OpenAI response builders
# ---------------------------------------------------------------------------

def gen_id(prefix: str = "chatcmpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def chunk_base(model: str) -> dict[str, Any]:
    return {
        "id": gen_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }


def content_chunk(model: str, content: str | None = None,
                  reasoning: str | None = None,
                  finish_reason: str | None = None,
                  role: str | None = None,
                  tool_calls: list[dict[str, Any]] | None = None,
                  include_usage_first: bool = False) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    c = chunk_base(model)
    c["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    return c


def usage_obj(prompt: int = 0, completion: int = 0) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def normalize_usage(u: dict[str, Any] | None) -> dict[str, int] | None:
    """Fill total_tokens when upstream omits it; tolerate missing fields."""
    if not isinstance(u, dict):
        return None
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    total = u.get("total_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": int(total) if total is not None else prompt + completion}


def final_response(model: str, content: str, reasoning: str | None = None,
                   tool_calls: list[dict[str, Any]] | None = None,
                   usage: dict[str, int] | None = None,
                   finish_reason: str = "stop") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["content"] = content or None
        finish_reason = "tool_calls"
    return {
        "id": gen_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": finish_reason,
        }],
        "usage": usage or usage_obj(),
    }


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1 token per CJK char, ~4 chars per latin word."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4
