"""Напоминания клиентам о предстоящих записях.

Слой синхронный и про Telegram ничего не знает: находит, КОМУ пора напомнить,
и фиксирует, что напоминание ушло. Отправка — в app/bot/notify.py.

Когда напоминание «пора»:

    start_at − h ≤ now < start_at − h + REMINDER_GRACE

Окно в 30 минут при тике планировщика раз в 5 минут даёт шесть попыток пережить
обрыв связи с Telegram. После окна напоминание уже не отправляется — иначе
«напоминание за сутки» могло бы прийти, когда до визита осталось два часа.

Запись, сделанная после момента напоминания (скажем, за 20 минут до визита),
этого напоминания не получает: клиент только что записался и так помнит.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKED
from app.models.notification import Notification
from app.services.app_settings_service import REMINDERS, get_setting
from app.services.schedule_service import local_tz

logger = logging.getLogger(__name__)

REMINDER_GRACE = timedelta(minutes=30)
MAX_HOURS = 168  # неделя
MAX_REMINDERS = 3


def kind_for(hours: int) -> str:
    return f"reminder_{hours}h"


def reminder_hours(db: Session) -> list[int]:
    """За сколько часов до визита напоминаем. Пустой список — напоминания выключены."""
    setting = get_setting(db, REMINDERS)
    if not setting.get("enabled", True):
        return []
    hours = set()
    for value in setting.get("hours_before", []):
        try:
            h = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < h <= MAX_HOURS:
            hours.add(h)
    return sorted(hours, reverse=True)


def due_reminders(db: Session, now: datetime) -> list[dict]:
    """Напоминания, которые пора отправить прямо сейчас."""
    items: list[dict] = []
    tz = local_tz()
    for hours in reminder_hours(db):
        kind = kind_for(hours)
        lead = timedelta(hours=hours)
        already_sent = (
            select(Notification.id)
            .where(Notification.booking_id == Booking.id, Notification.kind == kind)
            .exists()
        )
        rows = db.scalars(
            select(Booking)
            .join(Client, Client.id == Booking.client_id)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.employee),
                selectinload(Booking.service),
            )
            .where(
                Booking.status == BOOKED,
                Booking.start_at <= now + lead,
                Booking.start_at > now + lead - REMINDER_GRACE,
                Booking.created_at <= Booking.start_at - lead,
                Client.telegram_user_id.is_not(None),
                ~already_sent,
            )
            .order_by(Booking.start_at)
        ).all()
        for booking in rows:
            start = booking.start_at.astimezone(tz)
            items.append(
                {
                    "booking_id": booking.id,
                    "kind": kind,
                    "hours": hours,
                    "chat_id": booking.client.telegram_user_id,
                    "employee": booking.employee.name,
                    "service": booking.service.name,
                    "date": start.date(),
                    "start": start.strftime("%H:%M"),
                    "end": booking.end_at.astimezone(tz).strftime("%H:%M"),
                }
            )
    return items


def mark_sent(db: Session, booking_id: int, kind: str) -> bool:
    """Фиксирует отправку. False — такое напоминание уже записано, повторять не нужно."""
    db.add(Notification(booking_id=booking_id, kind=kind))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def parse_hours(raw: str) -> list[int]:
    """«24, 1» → [24, 1]. ValueError несёт текст, который можно показать в админке."""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError("Укажите хотя бы одно значение, например: 24, 1")
    try:
        hours = {int(p) for p in parts}
    except ValueError:
        raise ValueError("Часы — целые числа через запятую, например: 24, 1") from None
    if any(h < 1 or h > MAX_HOURS for h in hours):
        raise ValueError(f"Часы должны быть от 1 до {MAX_HOURS}")
    if len(hours) > MAX_REMINDERS:
        raise ValueError(f"Не больше {MAX_REMINDERS} напоминаний на одну запись")
    return sorted(hours, reverse=True)
