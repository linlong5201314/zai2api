"""End-to-end test with a mocked upstream Z.ai server (no real network)."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # isolate: open downstream, disable anonymous mode, no account tokens
    monkeypatch.setattr(config, "AUTH_TOKENS", [])
    monkeypatch.setattr(config, "ANONYMOUS_MODE", False)
    monkeypatch.setattr(config, "ZAI_TOKENS", [])
    monkeypatch.setattr(config, "CAPTCHA_ENABLED", False)

    from app.db import database
    from app.token_pool import pool

    async def _setup():
        database.path = str(tmp_path / "t.db")
        await database.connect()
        await database.upsert_token("eyJfake-account-token", "account")
        await pool.start()

    asyncio.run(_setup())
    with TestClient(app) as c:
        yield c
    asyncio.run(pool.stop())
    asyncio.run(database.close())


class MockUpstream:
    """in-process SSE upstream patched into ZaiClient"""

    @staticmethod
    def sse_response(text="Hello!", reasoning="thinking hard"):
        def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer eyJ"):
                return httpx.Response(401, json={"detail": "unauthorized"})
            # v2 wire format: thinking via delta_content increments,
            # answer via edit_content full snapshots
            events = [
                {"data": {"phase": "thinking",
                          "delta_content": f"<details>{reasoning}</details>"}},
                {"data": {"phase": "other",
                          "edit_content": f"<details>{reasoning}</details>{text}"}},
                {"data": {"phase": "done", "done": True,
                          "usage": {"prompt_tokens": 2, "completion_tokens": 3}}},
            ]
            body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})
        return handler


@pytest.fixture()
def mock_upstream(monkeypatch):
    from app.zai_client import ZaiClient
    real_init = ZaiClient.__init__

    def patched(self):
        real_init(self)
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(MockUpstream.sse_response()))

    monkeypatch.setattr(ZaiClient, "__init__", patched)
    from app.zai_client import client as zc
    # rebind the module-level singleton's client
    zc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(MockUpstream.sse_response()))
    yield
    asyncio.run(zc._client.aclose())


def test_models_endpoint(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "glm-4.7" in ids


def test_chat_nonstream(client, mock_upstream):
    r = client.post("/v1/chat/completions", json={
        "model": "glm-4.7", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    data = r.json()
    msg = data["choices"][0]["message"]
    assert msg["content"] == "Hello!"
    assert msg["reasoning_content"] == "thinking hard"
    assert data["usage"]["total_tokens"] == 5


def test_chat_stream(client, mock_upstream):
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "glm-4.7", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        raw = "".join(r.iter_text())
    assert '"reasoning_content": "thinking hard"' in raw.replace("\\u00a0", " ")
    assert '"content": "Hello!"' in raw
    assert raw.strip().endswith("data: [DONE]")


def test_auth_required_when_configured(client, mock_upstream, monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKENS", ["sk-secret"])
    r = client.post("/v1/chat/completions", json={
        "model": "glm-4.7", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401
    r2 = client.post("/v1/chat/completions", json={
        "model": "glm-4.7", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-secret"})
    assert r2.status_code == 200


def test_account_model_requires_account_pool(client, mock_upstream):
    r = client.post("/v1/chat/completions", json={
        "model": "glm-5", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200  # account token exists in fixture pool


def test_tools_emulated(client, mock_upstream, monkeypatch, tmp_path):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "CAPTCHA_ENABLED", False)
    from app.tools_emulator import parse_tool_calls  # noqa: F401
    # upstream returns a tool-call JSON instead of prose
    zc = __import__("app.zai_client", fromlist=["client"]).client

    tool_json = json.dumps({"name": "get_weather",
                            "arguments": {"city": "SF"}})
    async def fake_stream(self, token, payload, user_id=""):
        from app.converter import UpstreamEvent
        yield UpstreamEvent({"data": {"phase": "other",
                                      "edit_content": tool_json}})
        yield UpstreamEvent({"data": {"phase": "done", "done": True,
                                      "usage": {"prompt_tokens": 1,
                                                "completion_tokens": 2}}})
    monkeypatch.setattr(type(zc), "chat_stream", fake_stream)

    r = client.post("/v1/chat/completions", json={
        "model": "glm-4.7",
        "messages": [{"role": "user", "content": "weather in SF?"}],
        "tools": [{"type": "function", "function": {
            "name": "get_weather", "description": "w",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string"}}}}}]})
    assert r.status_code == 200
    msg = r.json()["choices"][0]["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
    assert r.json()["choices"][0]["finish_reason"] == "tool_calls"
