"""Function-calling emulation for Z.ai web chat.

Upstream has no native tool-call API. Strategy (proven by D3-vin/GLM-ZAI-2API):
1. Inject a system prompt describing the tools and requiring a strict JSON
   answer format when a call is needed.
2. Parse the model's text output with several fallback strategies.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .models import ChatMessage, ToolDef

TOOL_PROMPT_HEADER = """\
You have access to the following tools. When you need to use one, respond
with ONLY a JSON object in this exact format (no other text, no markdown):
{"name": "<tool name>", "arguments": <args as JSON object>}

If you don't need any tool, answer normally in plain text.

Tools:
"""

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
TOOL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
JSON_OBJ_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def build_tools_prompt(tools: list[ToolDef]) -> str:
    lines = [TOOL_PROMPT_HEADER]
    for t in tools:
        fn = t.function
        desc = fn.description or ""
        params = json.dumps(fn.parameters or {"type": "object", "properties": {}},
                            ensure_ascii=False)
        lines.append(f"- {fn.name}: {desc}\n  parameters: {params}")
    return "\n".join(lines)


def inject_tools(messages: list[dict[str, Any]], tools: list[ToolDef]) -> list[dict[str, Any]]:
    """Insert the tool instruction as the leading system message."""
    prompt = build_tools_prompt(tools)
    out = list(messages)
    if out and out[0].get("role") == "system":
        out[0] = dict(out[0])
        out[0]["content"] = out[0]["content"] + "\n\n" + prompt
    else:
        out.insert(0, {"role": "system", "content": prompt})
    return out


def _valid_call(obj: dict[str, Any]) -> bool:
    return isinstance(obj, dict) and "name" in obj


def _to_openai_calls(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a parsed {"name","arguments"} object to OpenAI tool_calls."""
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            json.loads(args)
        except json.JSONDecodeError:
            args = json.dumps({"input": args}, ensure_ascii=False)
    else:
        args = json.dumps(args or {}, ensure_ascii=False)
    return [{
        "id": f"call_{obj.get('name', 'fn')}",
        "type": "function",
        "function": {"name": obj["name"], "arguments": args},
    }]


def parse_tool_calls(text: str, tool_names: list[str] | None = None
                     ) -> Optional[list[dict[str, Any]]]:
    """Try several strategies to extract a tool call from model output.

    Returns OpenAI-format tool_calls, or None if the text is a plain answer.
    """
    if not text or not text.strip():
        return None
    candidates: list[str] = []
    m = TOOL_TAG_RE.search(text)
    if m:
        candidates.append(m.group(1))
    for m in JSON_BLOCK_RE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text.strip())
    for m in JSON_OBJ_RE.finditer(text):
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            calls = []
            for o in obj:
                if isinstance(o, dict) and _valid_call(o):
                    calls.extend(_to_openai_calls(o))
            if calls:
                return calls
            continue
        if not _valid_call(obj):
            continue
        if tool_names and obj["name"] not in tool_names:
            continue
        return _to_openai_calls(obj)
    return None


def tool_names(tools: list[ToolDef]) -> list[str]:
    return [t.function.name for t in tools]


def history_with_tools(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Post-injection view used by tests / re-requests."""
    return [m.model_dump(exclude_none=True) for m in messages]
