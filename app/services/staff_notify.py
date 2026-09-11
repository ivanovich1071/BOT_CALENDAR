"""Уведомления сотрудникам в Telegram о записях к ним.

Сотрудник привязывает свой Telegram одноразовой ссылкой из админки. Уведомление
кладётся в очередь (таблица outbox) сразу после изменения записи; отправляет
задача планировщика раз в минуту. Сбой Telegram не ломает запись и не теряет
уведомление: до MAX_ATTEMPTS попыток.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.texts import human_date
from app.models.booking import Booking
from app.models.employee import Employee
from app.models.outbox import OutboxMessage
from app.services.app_settings_service import get_setting, set_setting
from app.services.audit_service import log_action
from app.services.schedule_service import local_tz

logger = logging.getLogger(__name__)

STAFF_LINKS = "staff_links"  # {token: {"employee_id": 1, "expires": iso}}
LINK_TTL = timedelta(hours=24)
PAYLOAD_PREFIX = "staff_"
MAX_ATTEMPTS = 5

EVENT_TITLES = {
    "created": "🆕 Новая запись",
    "rescheduled": "🔄 Запись перенесена",
    "cancelled": "❌ Запись отменена",
    "updated": "✏️ Запись изменена",
    "restored": "↩️ Запись снова активна",
    "moved_out": "➡️ Запись передана другому специалисту",
}
SOURCE_LABELS = {"telegram": "бот, кнопки", "ai": "бот, ИИ-консультант", "admin": "админка"}


# ==== Привязка Telegram ====

def _live(links: dict) -> dict:
    now = datetime.now(timezone.utc)
    return {
        token: item
        for token, item in links.items()
        if isinstance(item, dict) and datetime.fromisoformat(item.get("expires", "1970-01-01T00:00:00+00:00")) > now
    }


def create_link_token(db: Session, employee_id: int) -> str:
    """Одноразовый токен для ссылки t.me/<бот>?start=staff_<токен>, живёт сутки."""
    links = _live(get_setting(db, STAFF_LINKS))
    token = secrets.token_urlsafe(16)
    links[token] = {
        "employee_id": employee_id,
        "expires": (datetime.now(timezone.utc) + LINK_TTL).isoformat(),
    }
    set_setting(db, STAFF_LINKS, links)
    return token


def consume_link_token(db: Session, token: str, telegram_user_id: int) -> str | None:
    """Привязывает Telegram к сотруднику. Имя сотрудника — или None, если ссылка негодна."""
    links = get_setting(db, STAFF_LINKS)
    item = _live(links).get(token)
    links.pop(token, None)
    set_setting(db, STAFF_LINKS, _live(links))
    if item is None:
        return None
    employee = db.get(Employee, item["employee_id"])
    if employee is None or employee.archived_at is not None:
        return None
    # Один Telegram — одному сотруднику: уникальный ключ в базе
    for other in db.scalars(
        select(Employee).where(Employee.telegram_user_id == telegram_user_id, Employee.id != employee.id)
    ):
        other.telegram_user_id = None
    db.flush()
    employee.telegram_user_id = telegram_user_id
    db.commit()
    log_action(
        db, actor=f"tg:{telegram_user_id}", action="employee.telegram_link",
        entity_type="employee", entity_id=employee.id,
    )
    return employee.name


# ==== Очередь уведомлений ====

def booking_text(booking: Booking, event: str, previous_start: datetime | None = None) -> str:
    start = booking.start_at.astimezone(local_tz())
    end = booking.end_at.astimezone(local_tz())
    lines = [
        EVENT_TITLES.get(event, "Запись"),
        "",
        f"💼 {booking.service.name}",
        f"📅 {human_date(start.date())}",
        f"🕐 {start:%H:%M} – {end:%H:%M}",
    ]
    if previous_start is not None:
        was = previous_start.astimezone(local_tz())
        lines.append(f"Было: {human_date(was.date())}, {was:%H:%M}")
    client = booking.client
    lines.append(f"👤 {client.name or 'клиент без имени'}" + (f", {client.phone}" if client.phone else ""))
    if booking.notes:
        lines.append(f"📝 {booking.notes}")
    lines.append(f"Источник: {SOURCE_LABELS.get(booking.source, booking.source)}")
    return "\n".join(lines)


def enqueue(
    db: Session,
    booking: Booking,
    event: str,
    *,
    actor: str | None = None,
    employee: Employee | None = None,
    previous_start: datetime | None = None,
) -> None:
    """Ставит уведомление сотруднику записи (или явно переданному).

    Пропускает сотрудника без Telegram и того, кто сам сделал это изменение.
    Любая ошибка — только в лог: уведомление не должно ломать запись.
    """
    try:
        target = employee or booking.employee
        if target is None or not target.telegram_user_id:
            return
        if actor and target.user is not None and target.user.login == actor:
            return
        db.add(OutboxMessage(chat_id=target.telegram_user_id, text=booking_text(booking, event, previous_start)))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Не удалось поставить уведомление по записи #%s", booking.id)


def pending(db: Session, limit: int = 30) -> list[dict]:
    rows = db.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.sent_at.is_(None), OutboxMessage.attempts < MAX_ATTEMPTS)
        .order_by(OutboxMessage.id)
        .limit(limit)
    ).all()
    return [{"id": r.id, "chat_id": r.chat_id, "text": r.text} for r in rows]


def mark_sent(db: Session, message_id: int) -> None:
    row = db.get(OutboxMessage, message_id)
    if row is not None:
        row.sent_at = datetime.now(timezone.utc)
        db.commit()


def mark_failed(db: Session, message_id: int, *, final: bool = False) -> None:
    """Неудача отправки. final — повторять бессмысленно (бот заблокирован, чата нет)."""
    row = db.get(OutboxMessage, message_id)
    if row is not None:
        row.attempts = MAX_ATTEMPTS if final else row.attempts + 1
        db.commit()
