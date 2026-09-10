"""Разбор свободной фразы клиента в намерение.

Модель возвращает структуру, а не действие. Что с намерением делать, решает бот,
а записывает booking_flow — ровно так же, как при нажатии кнопок.
"""

import datetime as dt
import logging
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from app.ai.prompts.intent import INTENT_SCHEMA, build_messages
from app.integrations.openrouter.client import OpenRouterError, complete_json

logger = logging.getLogger(__name__)


class Intent(BaseModel):
    action: Literal["book", "my_bookings", "info", "unknown"] = "unknown"
    service: str | None = None
    employee: str | None = None
    date: dt.date | None = None
    time_from: dt.time | None = None
    time_to: dt.time | None = None

    @field_validator("service", "employee", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    # Неудачная дата или время не должны обнулять всё намерение: клиент
    # всё равно хочет записаться, недостающее он выберет кнопками
    @field_validator("date", mode="before")
    @classmethod
    def _date_or_none(cls, value):
        if value in (None, "") or isinstance(value, dt.date):
            return value or None
        try:
            return dt.date.fromisoformat(str(value).strip())
        except ValueError:
            return None

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _time_or_none(cls, value):
        if value in (None, "") or isinstance(value, dt.time):
            return value or None
        try:
            return dt.time.fromisoformat(str(value).strip())
        except ValueError:
            return None


async def parse(text: str, services: list[str], employees: list[str], today: dt.date) -> Intent:
    """Намерение клиента. При любом сбое — action="unknown", без исключений."""
    messages = build_messages(text, services, employees, today)
    try:
        data = await complete_json(messages, INTENT_SCHEMA, name="booking_intent")
    except OpenRouterError as exc:
        # Текст клиента в лог не пишем — только причину
        logger.warning("AI: разбор не удался (%s)", exc)
        return Intent()
    try:
        return Intent.model_validate(data)
    except ValidationError:
        logger.warning("AI: ответ модели не прошёл схему")
        return Intent()
