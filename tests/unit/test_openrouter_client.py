"""Клиент OpenRouter: запрос с инструментами, разбор ответа, устойчивость к сбоям."""

import json

import httpx
import pytest

from app.config.settings import Settings
from app.integrations.openrouter import client as openrouter
from app.integrations.openrouter.client import MAX_TOKENS_CAP, OpenRouterError, chat

TOOL = {"type": "function", "function": {"name": "find_slots", "parameters": {"type": "object"}}}


@pytest.fixture
def settings(monkeypatch):
    s = Settings(_env_file=None, openrouter_api_key="sk-or-test", openrouter_max_tokens=2000)
    monkeypatch.setattr(openrouter, "get_settings", lambda: s)
    return s


def _reply(message: dict, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json={"choices": [{"message": message}]})
    )


async def test_запрос_несёт_инструменты_модель_и_потолок_токенов(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Здравствуйте"}}]})

    reply = await chat(
        [{"role": "user", "content": "x"}],
        tools=[TOOL],
        model="qwen/qwen3-235b-a22b-2507",
        transport=httpx.MockTransport(handler),
    )

    assert reply.content == "Здравствуйте" and reply.tool_calls == []
    assert seen["url"].endswith("/api/v1/chat/completions")
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["body"]["model"] == "qwen/qwen3-235b-a22b-2507"
    assert seen["body"]["tools"] == [TOOL] and seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["max_tokens"] == MAX_TOKENS_CAP


async def test_без_инструментов_поле_tools_не_отправляется(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await chat([], transport=httpx.MockTransport(handler))
    assert "tools" not in seen["body"]


async def test_вызов_инструмента_разбирается(settings):
    message = {
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "find_slots", "arguments": '{"service_id": 1}'}}
        ],
    }
    reply = await chat([], transport=_reply(message))
    assert reply.content == ""
    assert reply.tool_calls == [
        {"id": "c1", "type": "function", "function": {"name": "find_slots", "arguments": '{"service_id": 1}'}}
    ]


async def test_текст_частями_склеивается(settings):
    reply = await chat([], transport=_reply({"content": [{"type": "text", "text": "При"}, {"text": "вет"}]}))
    assert reply.content == "Привет"


@pytest.mark.parametrize("message", [{"content": ""}, {"content": None}, {}])
async def test_пустой_ответ_это_ошибка(settings, message):
    with pytest.raises(OpenRouterError):
        await chat([], transport=_reply(message))


async def test_ошибка_http_это_ошибка(settings):
    with pytest.raises(OpenRouterError):
        await chat([], transport=_reply({"content": "x"}, status=502))


async def test_таймаут_это_ошибка(settings):
    def handler(request):
        raise httpx.ReadTimeout("долго", request=request)

    with pytest.raises(OpenRouterError):
        await chat([], transport=httpx.MockTransport(handler))


async def test_без_ключа_запрос_не_отправляется(monkeypatch):
    s = Settings(_env_file=None, openrouter_api_key="")
    monkeypatch.setattr(openrouter, "get_settings", lambda: s)

    def handler(request):  # pragma: no cover — не должен вызываться
        raise AssertionError("запрос без ключа")

    with pytest.raises(OpenRouterError):
        await chat([], transport=httpx.MockTransport(handler))
