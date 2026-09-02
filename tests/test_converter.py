import json

from app.converter import (StreamAccumulator, UpstreamEvent,
                           build_upstream_messages, build_upstream_payload,
                           strip_reasoning_html)
from app.models import ChatMessage, ModelVariant


def ev(phase=None, delta="", edit=None, done=False, usage=None, error=None,
       edit_index=None):
    return UpstreamEvent({"data": {"phase": phase, "delta_content": delta,
                                   "edit_content": edit, "edit_index": edit_index,
                                   "done": done, "usage": usage, "error": error}})


# ---- thinking HTML stripping ----

def test_strip_reasoning_html():
    raw = ('<details type="reasoning" open="true"><summary>Thinking…</summary>'
           'let me think.\n</details>')
    assert strip_reasoning_html(raw).strip() == "let me think."


def test_strip_no_html_passthrough():
    assert strip_reasoning_html("plain") == "plain"


# ---- messages ----

def test_build_messages_basic():
    msgs = [ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hi")]
    out = build_upstream_messages(msgs)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_build_messages_tool_roundtrip():
    msgs = [
        ChatMessage(role="user", content="weather?"),
        ChatMessage(role="assistant", content="",
                    tool_calls=[{"id": "c1", "type": "function",
                                 "function": {"name": "get_weather",
                                              "arguments": "{\"city\":\"SF\"}"}}]),
        ChatMessage(role="tool", tool_call_id="c1",
                    content='{"temp": 20}'),
    ]
    out = build_upstream_messages(msgs)
    assert out[1]["role"] == "assistant"
    assert "get_weather" in out[1]["content"]
    assert out[2]["role"] == "user"
    assert "[Tool output]" in out[2]["content"]
    assert "20" in out[2]["content"]


# ---- payload ----

def test_payload_shape():
    v = ModelVariant("glm-4.7-thinking")
    payload = build_upstream_payload(v, [{"role": "user", "content": "x"}],
                                    web_search=False)
    assert payload["model"] == "glm-4.7"
    assert payload["features"]["enable_thinking"] is True
    assert payload["stream"] is True
    assert "chat_id" in payload and "id" in payload
    assert payload["background_tasks"]["title_generation"] is False
    assert "mcp_servers" not in payload


def test_payload_search_mcp():
    v = ModelVariant("glm-4.7-search")
    payload = build_upstream_payload(v, [])
    assert payload["mcp_servers"] == ["deep-web-search"]


def test_payload_unknown_model_passthrough():
    v = ModelVariant("glm-4.7")
    payload = build_upstream_payload(v, [])
    assert payload["model"] == "glm-4.7"  # known model -> registry id
    assert payload["model_item"]["id"] == "glm-4.7"

    v2 = ModelVariant("brand-new-model-x")
    payload2 = build_upstream_payload(v2, [])
    assert payload2["model"] == "brand-new-model-x"  # unknown -> passthrough


# ---- stream accumulator ----

def test_stream_reasoning_split():
    acc = StreamAccumulator("glm-4.7", think_mode="reasoning")
    raw = ('<details type="reasoning" open="true">'
           '<summary>Thinking…</summary>step one ')
    chunks = acc.feed(ev(phase="thinking", delta=raw))
    assert chunks and chunks[0]["choices"][0]["delta"]["reasoning_content"] == "step one "
    chunks = acc.feed(ev(phase="thinking", delta="step two"))
    assert chunks[0]["choices"][0]["delta"]["reasoning_content"] == "step two"
    chunks = acc.feed(ev(phase="thinking", delta="</details>"))
    assert chunks == []


def test_stream_answer_phase():
    # v2: answer text arrives as index-positioned edit pieces
    acc = StreamAccumulator("glm-4.7", think_mode="reasoning")
    c1 = acc.feed(ev(edit="Hello", edit_index=0))
    c2 = acc.feed(ev(edit=" world", edit_index=5))
    assert c1[0]["choices"][0]["delta"]["content"] == "Hello"
    assert c2[0]["choices"][0]["delta"]["content"] == " world"
    assert acc.answer_text == "Hello world"


def test_stream_done_and_usage():
    acc = StreamAccumulator("glm-4.7")
    acc.feed(ev(edit="ok"))
    usage = {"prompt_tokens": 3, "completion_tokens": 1}
    chunks = acc.feed(ev(done=True, usage=usage))
    assert chunks == []
    assert acc.usage == usage


def test_stream_edit_content_fallback():
    acc = StreamAccumulator("glm-4.7", think_mode="reasoning")
    # thinking via delta increments (wrapped in details)
    acc.feed(ev(phase="thinking", delta="<details><summary>Thinking…</summary>abc"))
    # answer arrives as full snapshot: text after </details> is the answer
    chunks = acc.feed(ev(phase="answer",
                         edit="<details><summary>Thinking…</summary></details>abcdef"))
    assert chunks[0]["choices"][0]["delta"]["content"] == "abcdef"
    assert acc.answer_text == "abcdef"


def test_upstream_event_error():
    acc = StreamAccumulator("glm-4.7")
    assert acc.feed(ev(error={"message": "boom"})) == []
    assert acc.error == {"message": "boom"}
