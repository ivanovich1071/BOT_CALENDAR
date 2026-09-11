"""Имя и описание бота в Telegram из профиля компании — то, что клиент видит до «Старт».

Меняет публичный профиль бота, поэтому вызывается только кнопкой в админке.
"""

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

# Ограничения Bot API
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 512
SHORT_DESCRIPTION_LIMIT = 120

INVITE = "Напишите вопрос своими словами или нажмите «Старт» — расскажу об услугах и подберу время у специалиста."


class BotProfileError(Exception):
    """Профиль не применён — текст показывается администратору."""


def profile_texts(company: dict) -> dict:
    name = (company.get("name") or "").strip()
    short = (company.get("tagline") or company.get("description") or "").strip()
    description = "\n\n".join(p for p in ((company.get("description") or "").strip(), INVITE) if p)
    return {
        "name": name[:NAME_LIMIT],
        "short_description": short[:SHORT_DESCRIPTION_LIMIT],
        "description": description[:DESCRIPTION_LIMIT],
    }


async def apply_bot_profile(token: str, company: dict) -> None:
    texts = profile_texts(company)
    if not texts["name"]:
        raise BotProfileError("Сначала укажите название компании")
    bot = Bot(token)
    try:
        await bot.set_my_name(name=texts["name"])
        await bot.set_my_description(description=texts["description"])
        await bot.set_my_short_description(short_description=texts["short_description"])
    except TelegramAPIError as exc:
        raise BotProfileError(f"Telegram отказал: {exc}") from exc
    finally:
        await bot.session.close()
