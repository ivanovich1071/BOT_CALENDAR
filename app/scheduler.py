"""Фоновые задачи внутри процесса API (APScheduler).

Отдельного сервиса намеренно нет: система эксплуатируется в одиночку, а лишний
процесс — лишняя точка отказа. От дублирования при нескольких воркерах uvicorn
защищает Redis-lock: задачу выполняет тот, кто первым взял блокировку.
"""

import asyncio
import logging

import redis as redis_lib
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.notify import send_due_reminders
from app.config.settings import get_settings
from app.db.database import SessionLocal
from app.services import calendar_service
from app.services.app_settings_service import GOOGLE_SYNC, get_setting

logger = logging.getLogger(__name__)

GOOGLE_SYNC_LOCK = "scheduler:google_sync"
REMINDERS_LOCK = "scheduler:reminders"

# Окно напоминания — 30 минут (reminder_service.REMINDER_GRACE): шесть попыток на обрыв связи
REMINDERS_INTERVAL_MINUTES = 5

_scheduler: AsyncIOScheduler | None = None


def _acquire(lock_key: str, ttl_seconds: int):
    """Берёт распределённую блокировку. None — Redis недоступен, идём без неё."""
    try:
        client = redis_lib.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1.5, socket_timeout=1.5
        )
        lock = client.lock(lock_key, timeout=ttl_seconds, blocking_timeout=0)
        return lock if lock.acquire(blocking=False) else False
    except Exception:  # noqa: BLE001
        logger.warning("Redis недоступен — задача %s выполняется без блокировки", lock_key)
        return None


def _release(lock) -> None:
    if lock:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 — истёкший lock освобождать уже нечего
            pass


def sync_google_job() -> None:
    """Догоняет правки, сделанные сотрудниками вручную в Google Calendar."""
    lock = _acquire(GOOGLE_SYNC_LOCK, ttl_seconds=300)
    if lock is False:
        return  # синхронизация уже идёт в другом воркере
    db = SessionLocal()
    try:
        updated = calendar_service.sync_all_accounts(db)
        if updated:
            logger.info("Синхронизация Google: обновлено броней — %s", updated)
    except Exception:  # noqa: BLE001 — планировщик не должен умирать от одной ошибки
        logger.exception("Синхронизация Google сорвалась")
    finally:
        db.close()
        _release(lock)


async def reminders_job() -> None:
    """Напоминания клиентам о визите — за сколько часов, решает настройка в админке."""
    token = get_settings().bot_token
    if not token:
        return
    lock = await asyncio.to_thread(_acquire, REMINDERS_LOCK, 240)
    if lock is False:
        return  # рассылка уже идёт в другом воркере
    # Без parse_mode: имя специалиста с «<» или «&» не должно ломать разметку
    bot = Bot(token)
    try:
        sent = await send_due_reminders(bot)
        if sent:
            logger.info("Напоминаний отправлено — %s", sent)
    except Exception:  # noqa: BLE001
        logger.exception("Рассылка напоминаний сорвалась")
    finally:
        await bot.session.close()
        await asyncio.to_thread(_release, lock)


def ai_history_job() -> None:
    """Диалоги с ИИ-консультантом хранятся ограниченное время."""
    from app.ai.agent import purge_old_messages

    db = SessionLocal()
    try:
        removed = purge_old_messages(db)
        if removed:
            logger.info("Удалено старых реплик диалогов — %s", removed)
    except Exception:  # noqa: BLE001
        logger.exception("Очистка диалогов сорвалась")
    finally:
        db.close()


def sync_interval_minutes() -> int:
    db = SessionLocal()
    try:
        return int(get_setting(db, GOOGLE_SYNC).get("interval_minutes", 10))
    finally:
        db.close()


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    minutes = sync_interval_minutes()
    _scheduler = AsyncIOScheduler(timezone=get_settings().timezone)
    _scheduler.add_job(
        sync_google_job,
        "interval",
        minutes=minutes,
        id="google_sync",
        max_instances=1,
        coalesce=True,          # проспали несколько тиков — выполняем один раз
        replace_existing=True,
    )
    _scheduler.add_job(
        reminders_job,
        "interval",
        minutes=REMINDERS_INTERVAL_MINUTES,
        id="reminders",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.add_job(
        ai_history_job,
        "interval",
        hours=24,
        id="ai_history",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Планировщик запущен: синхронизация Google каждые %s мин, напоминания каждые %s мин",
        minutes,
        REMINDERS_INTERVAL_MINUTES,
    )
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
