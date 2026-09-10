"""Клиент OpenRouter: один запрос — один JSON-объект по схеме.

Модель здесь ничего не решает — она переводит текст в структуру. Любая проблема
(нет ключа, таймаут, не тот формат) превращается в OpenRouterError, и вызывающий
откатывается на кнопки.
"""

import json
import logging

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
# Намерение укладывается в сотню токенов; потолок — защита от счёта за «разговорчивость»
MAX_TOKENS_CAP = 400


class OpenRouterError(Exception):
    """Пригодного к использованию ответа нет."""


async def complete_json(
    messages: list[dict],
    schema: dict,
    *,
    name: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    s = get_settings()
    if not s.openrouter_api_key:
        raise OpenRouterError("OPENROUTER_API_KEY не заполнен")

    payload = {
        "model": s.openrouter_model,
        "messages": messages,
        "temperature": s.openrouter_temperature,
        "max_tokens": min(s.openrouter_max_tokens, MAX_TOKENS_CAP),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
    }
    headers = {
        "Authorization": f"Bearer {s.openrouter_api_key}",
        "HTTP-Referer": s.app_base_url,
        "X-Title": "BOT_CALENDAR",
    }
    try:
        async with httpx.AsyncClient(
            base_url=s.openrouter_base_url, timeout=TIMEOUT_SECONDS, transport=transport
        ) as client:
            response = await client.post("/chat/completions", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise OpenRouterError(f"сеть: {type(exc).__name__}") from exc

    if response.status_code != 200:
        raise OpenRouterError(f"HTTP {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError("неожиданная форма ответа") from exc
    return loads_object(content)


def loads_object(content) -> dict:
    """JSON-объект из ответа модели; терпит обёртку ```json … ```."""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError("пустой ответ")
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise OpenRouterError("в ответе нет JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OpenRouterError("битый JSON") from exc
    if not isinstance(data, dict):
        raise OpenRouterError("JSON — не объект")
    return data
