"""Разбор фразы: запрос к OpenRouter, устойчивость к мусору и сбоям."""

import datetime as dt
import json

import httpx
import pytest

from app.ai import intent as ai_intent
from app.ai.prompts.intent import CALENDAR_DAYS, MAX_MESSAGE_CHARS, build_messages
from app.config.settings import Settings
from app.integrations.openrouter import client as openrouter
from app.integrations.openrouter.client import MAX_TOKENS_CAP, OpenRouterError, complete_json

TODAY = dt.date(2026, 9, 10)  # четверг


@pytest.fixture
def settings(monkeypatch):
    s = Settings(_env_file=None, openrouter_api_key="sk-or-test", openrouter_max_tokens=2000)
    monkeypatch.setattr(openrouter, "get_settings", lambda: s)
    return s


def _reply(content, status=200):
    body = {"choices": [{"message": {"content": content}}]}
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


INTENT = {
    "action": "book",
    "service": "Консультация",
    "employee": None,
    "date": "2026-09-11",
    "time_from": "12:00",
    "time_to": "17:00",
}


# ==== Клиент OpenRouter ====

async def test_запрос_уходит_со_строгой_схемой_и_потолком_токенов(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(INTENT)}}]})

    data = await complete_json(
        [{"role": "user", "content": "x"}], {"type": "object"}, name="t",
        transport=httpx.MockTransport(handler),
    )
    assert data == INTENT
    assert seen["url"].endswith("/api/v1/chat/completions")
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert seen["body"]["max_tokens"] == MAX_TOKENS_CAP


async def test_обёртка_markdown_не_мешает(settings):
    content = "```json\n" + json.dumps(INTENT) + "\n```"
    assert await complete_json([], {}, name="t", transport=_reply(content)) == INTENT


@pytest.mark.parametrize("content", ["не json", "{битый", "[1, 2]", ""])
async def test_мусор_вместо_json_это_ошибка(settings, content):
    with pytest.raises(OpenRouterError):
        await complete_json([], {}, name="t", transport=_reply(content))


async def test_ошибка_http_это_ошибка(settings):
    with pytest.raises(OpenRouterError):
        await complete_json([], {}, name="t", transport=_reply("{}", status=502))


async def test_таймаут_это_ошибка(settings):
    def handler(request):
        raise httpx.ReadTimeout("долго", request=request)

    with pytest.raises(OpenRouterError):
        await complete_json([], {}, name="t", transport=httpx.MockTransport(handler))


async def test_без_ключа_запрос_не_отправляется(monkeypatch):
    s = Settings(_env_file=None, openrouter_api_key="")
    monkeypatch.setattr(openrouter, "get_settings", lambda: s)

    def handler(request):  # pragma: no cover — не должен вызываться
        raise AssertionError("запрос без ключа")

    with pytest.raises(OpenRouterError):
        await complete_json([], {}, name="t", transport=httpx.MockTransport(handler))


# ==== Намерение ====

def _fake_complete(result):
    async def fake(messages, schema, *, name, transport=None):
        if isinstance(result, Exception):
            raise result
        return result

    return fake


async def test_намерение_разбирается(monkeypatch):
    monkeypatch.setattr(ai_intent, "complete_json", _fake_complete(INTENT))
    parsed = await ai_intent.parse("на консультацию завтра после обеда", ["Консультация"], [], TODAY)
    assert parsed.action == "book" and parsed.service == "Консультация"
    assert parsed.date == dt.date(2026, 9, 11)
    assert parsed.time_from == dt.time(12, 0) and parsed.time_to == dt.time(17, 0)


async def test_кривая_дата_не_обнуляет_намерение(monkeypatch):
    monkeypatch.setattr(
        ai_intent, "complete_json", _fake_complete({**INTENT, "date": "завтра", "time_from": "9 утра"})
    )
    parsed = await ai_intent.parse("…", [], [], TODAY)
    assert parsed.action == "book" and parsed.date is None and parsed.time_from is None


async def test_неизвестное_действие_это_unknown(monkeypatch):
    monkeypatch.setattr(ai_intent, "complete_json", _fake_complete({**INTENT, "action": "cancel_all"}))
    assert (await ai_intent.parse("…", [], [], TODAY)).action == "unknown"


async def test_сбой_openrouter_это_unknown(monkeypatch):
    monkeypatch.setattr(ai_intent, "complete_json", _fake_complete(OpenRouterError("таймаут")))
    assert (await ai_intent.parse("…", [], [], TODAY)).action == "unknown"


# ==== Промпт ====

def test_промпт_несёт_календарь_и_справочники():
    [system, user] = build_messages("Хочу " + "а" * 1000, ["Консультация"], ["Иванов"], TODAY)
    content = system["content"]
    assert "2026-09-10 — чт, сегодня" in content
    assert "2026-09-11 — пт, завтра" in content
    assert (TODAY + dt.timedelta(days=CALENDAR_DAYS - 1)).isoformat() in content
    assert (TODAY + dt.timedelta(days=CALENDAR_DAYS)).isoformat() not in content
    assert "- Консультация" in content and "- Иванов" in content
    assert len(user["content"]) == MAX_MESSAGE_CHARS
