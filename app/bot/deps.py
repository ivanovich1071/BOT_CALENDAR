"""Общие зависимости хендлеров."""

from aiogram.types import User

from app.bot import services
from app.bot.db import run_db


def display_name(user: User) -> str:
    parts = [p for p in (user.first_name, user.last_name) if p]
    return " ".join(parts) or (user.username or "Клиент")


async def current_client(user: User) -> dict:
    """Клиент по Telegram-профилю. Создаётся при первом обращении, не только на /start."""
    return await run_db(
        services.get_or_create_client, user.id, user.username, display_name(user)
    )
