"""Format conversion: OpenAI messages <-> Z.ai upstream payloads & SSE streams.

Upstream protocol notes (verified against chat.z.ai, Sep 2026):
- POST /api/chat/completions with {stream:true, chat_id, id, model, messages,
  params, features:{enable_thinking}, background_tasks, model_item, variables}
- SSE events: {"type": ..., "data": {delta_content, edit_content, phase, done,
  usage, error, inner}}; phase "thinking" carries reasoning text wrapped in
  <details type="reasoning"><summary>...</summary>...</details> HTML.
- Web search is toggled via mcp_servers: ["deep-web-search"].
"""
from __future__ import annotations

import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from . import config
from .models import ChatMessage, ChatRequest, ModelVariant, chunk_base, content_chunk

DETAILS_OPEN_RE = re.compile(r"<details[^>]*>", re.IGNORECASE)
SUMMARY_RE = re.compile(r"<summary[^>]*>.*?</summary>", re.IGNORECASE | re.DOTALL)
DETAILS_CLOSE = "</details>"

SEARCH_MCP = "deep-web-search"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")


def jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT payload section (base64url, no padding)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}


def upstream_headers(token: str, stream: bool = True) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "User-Agent": BROWSER_UA,
        "X-FE-Version": "prod-fe-1.1.41",
        "Origin": config.ZAI_BASE_URL,
        "Referer": config.ZAI_BASE_URL + "/",
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
        "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    return h


# ---------------------------------------------------------------------------
# thinking-phase HTML stripping
# ---------------------------------------------------------------------------

def strip_reasoning_html(text: str) -> str:
    """Remove <details>/<summary> reasoning wrapper, keep inner text."""
    if "<details" not in text and "<summary" not in text:
        return text
    text = DETAILS_OPEN_RE.sub("", text)
    text = SUMMARY_RE.sub("", text)
    text = text.replace(DETAILS_CLOSE, "")
    return text


def _cut_partial_tag(text: str) -> str:
    """Hold back a trailing incomplete HTML tag so emission stays stable."""
    i = text.rfind("<")
    if i != -1 and ">" not in text[i:]:
        return text[:i]
    return text


# ---------------------------------------------------------------------------
# message conversion
# ---------------------------------------------------------------------------

def _image_data_url(url: str) -> str:
    if url.startswith("data:"):
        return url
    resp = httpx.get(url, timeout=30, follow_redirects=True,
                     headers={"User-Agent": BROWSER_UA})
    resp.raise_for_status()
    if len(resp.content) > config.MAX_IMAGE_SIZE:
        raise ValueError(f"image too large: {len(resp.content)} bytes")
    mime = resp.headers.get("content-type", "image/png").split(";")[0]
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(resp.content).decode()
    return f"data:{mime};base64,{b64}"


def parts_to_content(parts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI content parts into (text, image_data_urls)."""
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    for p in parts:
        ptype = p.get("type")
        if ptype == "text" and p.get("text"):
            texts.append(p["text"])
        elif ptype == "image_url":
            iu = p.get("image_url") or {}
            url = iu.get("url") if isinstance(iu, dict) else None
            if url:
                images.append({"type": "image_url",
                               "image_url": {"url": _image_data_url(url)}})
    return "\n".join(texts), images


def tool_history_to_text(message: ChatMessage) -> str:
    """Serialize assistant tool_calls into plain text the upstream can read."""
    lines = ["I previously made these function calls:"]
    for tc in message.tool_calls or []:
        fn = (tc.get("function") or {})
        lines.append(f'- {fn.get("name", "unknown")}({fn.get("arguments", "{}")})')
    return "\n".join(lines)


def build_upstream_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """OpenAI ChatMessage list -> Z.ai messages (Open WebUI style).

    - system passes through
    - vision parts become content arrays with base64 data URLs
    - assistant.tool_calls become text (upstream has no native tool calls)
    - role:"tool" becomes a user message labelled as tool output
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role if m.role in ("system", "user", "assistant") else "user"

        if m.tool_calls and role == "assistant":
            body = (m.content if isinstance(m.content, str) else "") or ""
            out.append({"role": "assistant",
                        "content": (body + "\n\n" + tool_history_to_text(m)).strip()})
            continue

        if role == "tool" or m.tool_call_id:
            body = m.content if isinstance(m.content, str) else json.dumps(
                [p.model_dump() for p in (m.content or [])], ensure_ascii=False)
            out.append({"role": "user",
                        "content": f"[Tool output]\n{body}"})
            continue

        if isinstance(m.content, list):
            parts = [p.model_dump(exclude_none=True) for p in m.content]
            text, images = parts_to_content(parts)
            if images:
                content: Any = [{"type": "text", "text": text}] + images
            else:
                content = text
            out.append({"role": role, "content": content})
        else:
            out.append({"role": role, "content": m.content or ""})
    return out


def build_upstream_payload(variant: ModelVariant, messages: list[dict[str, Any]],
                           web_search: bool = False) -> dict[str, Any]:
    up = variant.upstream()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload: dict[str, Any] = {
        "stream": True,
        "chat_id": str(uuid.uuid4()),
        "id": str(uuid.uuid4()),
        "model": up["model"],
        "messages": messages,
        "params": {},
        "features": {"enable_thinking": variant.effective_thinking()},
        "background_tasks": {"title_generation": False, "tags_generation": False},
        "model_item": {"id": up["id"], "name": up["name"], "owned_by": "z.ai"},
        "tool_servers": [],
        "variables": {
            "{{USER_NAME}}": "User",
            "{{USER_LOCATION}}": "",
            "{{CURRENT_DATETIME}}": now,
        },
    }
    if web_search or variant.search:
        payload["mcp_servers"] = [SEARCH_MCP]
    return payload


# ---------------------------------------------------------------------------
# upstream SSE parsing
# ---------------------------------------------------------------------------

class UpstreamEvent:
    __slots__ = ("phase", "delta", "edit", "edit_index", "done", "usage", "error")

    def __init__(self, raw: dict[str, Any]):
        data = raw.get("data") or {}
        self.phase: Optional[str] = data.get("phase")
        self.delta: str = data.get("delta_content") or ""
        self.edit: Optional[str] = data.get("edit_content")
        self.edit_index: Optional[int] = data.get("edit_index")
        self.done: bool = bool(data.get("done")) or raw.get("type") == "done"
        self.usage: Optional[dict[str, Any]] = data.get("usage")
        self.error: Any = data.get("error")


async def parse_upstream_sse(resp: httpx.Response) -> AsyncIterator[UpstreamEvent]:
    """Iterate `data: {...}` lines from an upstream streaming response."""
    buffer = ""
    async for chunk in resp.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":") or line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                raw = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            yield UpstreamEvent(raw)


# ---------------------------------------------------------------------------
# stream normalization: upstream events -> OpenAI chunks
# ---------------------------------------------------------------------------

class StreamAccumulator:
    """Convert raw upstream pieces into OpenAI chunk dicts, splitting reasoning.

    v2 stream format (verified live):
    - delta_content: thinking increments, wrapped in
      <details type="reasoning"> ... </details> across events
    - edit_content + edit_index: INCREMENTAL text pieces positioned at
      edit_index in the full assistant text (thinking HTML + answer);
      concatenating them in order rebuilds the full message
    - phase is unreliable ("other"/"thinking"/"answer" mixed usage)

    Reasoning = content inside the <details> block (from delta view);
    answer = text after </details> (from the rebuilt full view).
    """

    def __init__(self, model: str, think_mode: str | None = None):
        self.model = model
        self.think_mode = think_mode or config.THINK_TAGS_MODE
        self._raw_think = ""
        self._think_emitted = ""
        self._full = ""          # full assistant text rebuilt from edit pieces
        self._answer_emitted = ""
        self._saw_edit = False
        self._details_seen = False
        self.reasoning_text = ""
        self.answer_text = ""
        self.usage: dict[str, Any] | None = None
        self.error: Any = None

    def _emit_think_delta(self) -> str:
        cleaned = strip_reasoning_html(self._raw_think)
        cleaned = _cut_partial_tag(cleaned)
        if cleaned.startswith(self._think_emitted):
            delta = cleaned[len(self._think_emitted):]
        else:
            delta = ""
        if delta:
            self._think_emitted = cleaned
        return delta

    def _feed_edit_piece(self, piece: str, index: Optional[int]) -> None:
        """Append an incremental edit piece at its position in the full text."""
        if index is None or index <= len(self._full):
            if index == len(self._full) or index is None:
                self._full += piece
            # index < len: stale duplicate — ignore
            return
        # gap: fill with spaces to keep positions, then append
        self._full += " " * (index - len(self._full)) + piece
        self._saw_edit = True

    def _answer_from_full(self, full: str) -> str:
        """Extract the answer part from the rebuilt full assistant text.

        The edit stream typically starts mid-way (skipping the leading
        <details type="reasoning" ...> prefix), so split on the closing
        tag instead of the opening one. While thinking is still open
        (delta view saw <details but no </details> yet), there is no
        answer to emit.
        """
        idx = full.find("</details>")
        if idx != -1:
            return full[idx + len("</details>"):]
        if self._details_seen:
            return ""  # thinking block still open
        return full

    def feed(self, ev: UpstreamEvent) -> list[dict[str, Any]]:
        """Return OpenAI chunk dicts for this event."""
        if ev.error:
            self.error = ev.error
            return []
        if ev.usage:
            self.usage = ev.usage
        if ev.done or ev.phase == "done":
            return []

        chunks: list[dict[str, Any]] = []

        # reasoning: accumulate delta_content increments (details view)
        if ev.delta:
            if "<details" in ev.delta:
                self._details_seen = True
            self._raw_think += ev.delta
            think_delta = self._emit_think_delta()
            if think_delta:
                self.reasoning_text += think_delta
                if self.think_mode == "reasoning":
                    chunks.append(content_chunk(self.model, reasoning=think_delta))
                elif self.think_mode == "think":
                    chunks.append(content_chunk(self.model, content=think_delta))

        # answer: rebuild full text from edit pieces, then split
        if ev.edit:
            self._feed_edit_piece(ev.edit, ev.edit_index)
            ans = self._answer_from_full(self._full)
            if ans:
                if ans.startswith(self._answer_emitted):
                    delta = ans[len(self._answer_emitted):]
                else:
                    delta = ans[len(self._answer_emitted):] \
                        if len(ans) > len(self._answer_emitted) else ""
                    if not ans.startswith(self._answer_emitted):
                        # rewritten text — re-emit conservatively
                        delta = ans
                        self.answer_text = ""
                self._answer_emitted = ans
                if delta:
                    self.answer_text += delta
                    chunks.append(content_chunk(self.model, content=delta))
        return chunks
