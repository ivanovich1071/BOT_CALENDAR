"""Telegram-бот. Запуск: python -m app.bot.main

Long polling: вебхук требует публичного HTTPS-адреса, которого на этапе
разработки нет. При деплое переключается на вебхук без правки хендлеров.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import appointments, booking, start
from app.config.network import prefer_ipv4
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Связь с api.telegram.org бывает прерывистой (DPI, мобильный интернет):
# запрос то проходит за 0.1 c, то отваливается по таймауту. Ронять из-за
# этого весь процесс нельзя — сам polling переживает обрывы и переподключается,
# спотыкается только старт.
STARTUP_ATTEMPTS = 5
STARTUP_BACKOFF_SECONDS = 3


async def with_retry(
    action: Callable[[], Awaitable[Any]],
    what: str,
    attempts: int = STARTUP_ATTEMPTS,
    backoff: float = STARTUP_BACKOFF_SECONDS,
) -> Any:
    """Повторяет сетевой вызов при обрыве связи с Telegram."""
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except TelegramNetworkError as exc:
            if attempt == attempts:
                raise
            logger.warning(
                "%s: связь с Telegram оборвалась (%s), попытка %s из %s",
                what, type(exc).__name__, attempt, attempts,
            )
            await asyncio.sleep(backoff)
    return None  # недостижимо: последняя попытка либо вернёт, либо бросит


def build_storage(redis_url: str):
    """Состояние диалогов в Redis: после перезапуска бота клиент не теряет шаг.

    Redis недоступен — работаем на памяти процесса, бот важнее сохранности FSM.
    """
    try:
        import redis as redis_lib
        from aiogram.fsm.storage.redis import RedisStorage

        client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        client.close()
        return RedisStorage.from_url(redis_url)
    except Exception:  # noqa: BLE001
        logger.warning("Redis недоступен — состояние диалогов держим в памяти процесса")
        return MemoryStorage()


def build_dispatcher(storage=None) -> Dispatcher:
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(booking.router)
    dp.include_router(appointments.router)
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    # Порядок адресов важен для google-api-python-client (см. app/config/network.py).
    # aiohttp резолвит сам и в этой сортировке не нуждается — у него Happy Eyeballs.
    if settings.prefer_ipv4:
        prefer_ipv4()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не заполнен в .env — см. docs/TELEGRAM_SETUP.md")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(build_storage(settings.redis_url))

    try:
        me = await with_retry(bot.get_me, "Проверка токена")
        logger.info("Бот @%s запущен, слушаю обновления", me.username)
        # Хвост необработанных обновлений после простоя клиенту не нужен
        await with_retry(
            lambda: bot.delete_webhook(drop_pending_updates=True), "Сброс вебхука"
        )
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
