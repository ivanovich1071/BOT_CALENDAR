"""Клиент OpenRouter: диалог с вызовом инструментов.

Модель отвечает текстом или просит вызвать инструмент — что с этим делать, решает
агент (app/ai/agent.py). Любая проблема (нет ключа, таймаут, не тот формат)
превращается в OpenRouterError, и бот откатывается на кнопки.
"""

import logging
from dataclasses import dataclass, field

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 40
# Ответ консультанта — несколько абзацев; потолок — защита от счёта за «разговорчивость»
MAX_TOKENS_CAP = 900


class OpenRouterError(Exception):
    """Пригодного к использованию ответа нет."""


@dataclass
class ChatReply:
    content: str = ""
    # [{"id", "type": "function", "function": {"name", "arguments": "<json>"}}]
    tool_calls: list[dict] = field(default_factory=list)


def _text(content) -> str:
    """Текст ответа: у части провайдеров content приходит списком фрагментов."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


async def chat(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatReply:
    s = get_settings()
    if not s.openrouter_api_key:
        raise OpenRouterError("OPENROUTER_API_KEY не заполнен")

    payload: dict = {
        "model": model or s.openrouter_model,
        "messages": messages,
        "temperature": s.openrouter_temperature if temperature is None else temperature,
        "max_tokens": min(s.openrouter_max_tokens, MAX_TOKENS_CAP),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
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
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError("неожиданная форма ответа") from exc

    calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        if not function.get("name"):
            continue
        calls.append(
            {
                "id": raw.get("id") or f"call_{len(calls)}",
                "type": "function",
                "function": {"name": function["name"], "arguments": function.get("arguments") or "{}"},
            }
        )
    content = _text(message.get("content"))
    if not content.strip() and not calls:
        raise OpenRouterError("пустой ответ")
    return ChatReply(content=content, tool_calls=calls)
