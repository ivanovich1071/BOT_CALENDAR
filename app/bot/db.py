"""Мост между асинхронным aiogram и синхронными сервисами.

Сессия SQLAlchemy живёт целиком внутри рабочего потока: делить её между
потоками нельзя, а возвращать ORM-объекты наружу бессмысленно — после закрытия
сессии они непригодны. Поэтому функции из app/bot/services.py отдают словари.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from app.db.database import SessionLocal


async def run_db(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Выполняет fn(db, *args) в пуле потоков и закрывает сессию."""

    def call() -> Any:
        db = SessionLocal()
        try:
            return fn(db, *args, **kwargs)
        finally:
            db.close()

    return await asyncio.to_thread(call)
