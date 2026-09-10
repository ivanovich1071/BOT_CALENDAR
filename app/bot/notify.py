"""Отправка напоминаний в Telegram.

Вызывается планировщиком из процесса API. Отдельный экземпляр Bot для отправки
не конфликтует с polling в процессе бота: Telegram запрещает два одновременных
getUpdates, а sendMessage можно отправлять откуда угодно.
"""

import logging
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from app.bot import texts
from app.bot.db import run_db
from app.bot.keyboards.menu import booking_actions
from app.services import reminder_service
from app.services.schedule_service import local_tz

logger = logging.getLogger(__name__)


async def send_due_reminders(bot, now: datetime | None = None) -> int:
    """Отправляет всё, что пора. Возвращает число доставленных напоминаний."""
    moment = now or datetime.now(timezone.utc)
    items = await run_db(reminder_service.due_reminders, moment)
    today = moment.astimezone(local_tz()).date()

    sent = 0
    for item in items:
        text = texts.REMINDER.format(
            when=texts.reminder_when(item["hours"], (item["date"] - today).days),
            employee=item["employee"],
            service=item["service"],
            date=texts.human_date(item["date"]),
            start=item["start"],
            end=item["end"],
        )
        try:
            await bot.send_message(
                item["chat_id"], text, reply_markup=booking_actions(item["booking_id"])
            )
        except TelegramForbiddenError:
            # Клиент заблокировал бота: повторы в пределах окна безвредны и ограничены
            logger.info("Напоминание по записи #%s не доставить: бот заблокирован", item["booking_id"])
            continue
        except TelegramAPIError as exc:
            logger.warning(
                "Напоминание по записи #%s не ушло (%s), повторю на следующем тике",
                item["booking_id"],
                type(exc).__name__,
            )
            continue
        if await run_db(reminder_service.mark_sent, item["booking_id"], item["kind"]):
            sent += 1
    return sent
