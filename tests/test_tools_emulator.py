from app.tools_emulator import (build_tools_prompt, inject_tools,
                                parse_tool_calls)


def test_parse_fenced_json():
    text = 'Let me check.\n```json\n{"name": "get_weather", "arguments": {"city": "SF"}}\n```'
    calls = parse_tool_calls(text, ["get_weather"])
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_weather"
    assert '"SF"' in calls[0]["function"]["arguments"]


def test_parse_raw_json():
    text = '{"name": "search", "arguments": {"q": "glm"}}'
    calls = parse_tool_calls(text, ["search"])
    assert calls[0]["function"]["name"] == "search"


def test_parse_tool_tag():
    text = '<tool_call>{"name": "fn1", "arguments": {"a": 1}}</tool_call>'
    calls = parse_tool_calls(text, ["fn1"])
    assert calls[0]["function"]["name"] == "fn1"


def test_plain_text_returns_none():
    assert parse_tool_calls("The weather in SF is sunny.", ["get_weather"]) is None
    assert parse_tool_calls("", ["x"]) is None


def test_name_mismatch_returns_none():
    text = '{"name": "evil_tool", "arguments": {}}'
    assert parse_tool_calls(text, ["get_weather"]) is None


def test_arguments_as_string():
    text = '{"name": "fn", "arguments": "{\\"q\\": 1}"}'
    calls = parse_tool_calls(text, ["fn"])
    assert calls[0]["function"]["arguments"] == '{"q": 1}'


def test_inject_tools_appends_system():
    msgs = [{"role": "user", "content": "hi"}]
    out = inject_tools(msgs, [])
    assert out[0]["role"] == "system"
    assert "Tools:" in out[0]["content"]


def test_inject_tools_merges_existing_system():
    msgs = [{"role": "system", "content": "be nice"}]
    out = inject_tools(msgs, [])
    assert out[0]["content"].startswith("be nice")


def test_build_tools_prompt_lists_params():
    from app.models import FunctionDef, ToolDef
    tools = [ToolDef(function=FunctionDef(
        name="get_weather", description="Get weather",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}}))]
    p = build_tools_prompt(tools)
    assert "get_weather" in p and "Get weather" in p and "city" in p
