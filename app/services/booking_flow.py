"""Единая точка бронирования: расписание + Google Calendar + БД + аудит.

Этим слоем пользуются и админка, и Telegram-бот, поэтому правила записи
не разъезжаются между интерфейсами.

Правило: **сбой Google не отменяет бронь**. Запись коммитится в PostgreSQL
(бизнес-источник истины), неудача записи в Google уходит в audit_logs
с action="google.push_failed", а вызывающий получает признак:

    None  — календарь не подключён, событие и не ожидалось (молча)
    True  — событие записано в Google
    False — Google не ответил, запись живёт только в БД
"""

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.booking import Booking
from app.models.enums import BOOKED, CANCELLED
from app.models.service import Service
from app.services import booking_service, calendar_service
from app.services.audit_service import log_action
from app.services.schedule_service import free_slots, local_tz

logger = logging.getLogger(__name__)

GoogleResult = bool | None


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Границы локальных суток в UTC — в этих рамках спрашиваем занятость у Google."""
    start = datetime.combine(day, time.min, tzinfo=local_tz()).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _google_busy(db: Session, employee_id: int, day: date) -> list[tuple[datetime, datetime]]:
    """Занятость сотрудника в Google за сутки. Любая ошибка → пустой список и лог."""
    start_utc, end_utc = _day_bounds_utc(day)
    try:
        return calendar_service.get_busy_intervals(db, employee_id, start_utc, end_utc)
    except Exception:  # noqa: BLE001 — Google не должен блокировать выдачу слотов
        logger.exception("Не удалось получить занятость Google (сотрудник #%s)", employee_id)
        return []


def _without_own_event(
    busy: list[tuple[datetime, datetime]], booking: Booking | None
) -> list[tuple[datetime, datetime]]:
    """Убирает из занятости событие самой переносимой записи.

    Иначе бронь конфликтует сама с собой: её событие лежит в Google и закрывает
    соседние слоты при переносе внутри того же дня.
    """
    if booking is None:
        return busy
    own_start = calendar_service.as_utc(booking.start_at)
    own_end = calendar_service.as_utc(booking.end_at)
    return [(s, e) for s, e in busy if not (s == own_start and e == own_end)]


def _safe_push(db: Session, booking: Booking, operation: str, push) -> GoogleResult:
    """Пишет изменение в Google. Ошибка не выбрасывается наверх, а попадает в аудит."""
    try:
        done = push(db, booking)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Google %s не удался для записи #%s", operation, booking.id)
        log_action(
            db,
            actor="system",
            action="google.push_failed",
            entity_type="booking",
            entity_id=booking.id,
            details={"operation": operation},
        )
        return False
    if done in (None, False):
        # Календарь не подключён (push вернул None/False) — это не ошибка,
        # система рассчитана на работу и без Google.
        return None
    return True


# ==== Чтение ====

def available_slots(
    db: Session,
    employee_id: int,
    service_id: int,
    day: date,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    """Свободные слоты: расписание − брони в БД − занятость Google."""
    service = db.get(Service, service_id)
    if service is None or not service.is_active:
        return []
    busy = _google_busy(db, employee_id, day)
    if exclude_booking_id:
        busy = _without_own_event(busy, db.get(Booking, exclude_booking_id))
    return free_slots(
        db, employee_id, service.duration_minutes, day, busy, exclude_booking_id or None
    )


# ==== Запись ====

def create(
    db: Session,
    *,
    client_id: int,
    employee_id: int,
    service_id: int,
    start_at: datetime,
    source: str,
    actor: str,
    notes: str | None = None,
    user_id: int | None = None,
) -> tuple[Booking, GoogleResult]:
    """Создаёт запись в БД и заводит событие в календаре сотрудника."""
    busy = _google_busy(db, employee_id, start_at.astimezone(local_tz()).date())
    booking = booking_service.create_booking(
        db,
        client_id=client_id,
        employee_id=employee_id,
        service_id=service_id,
        start_at=start_at,
        source=source,
        notes=notes,
        busy_intervals=busy,
    )
    google = _safe_push(db, booking, "create", calendar_service.push_booking)
    log_action(
        db,
        actor=actor,
        action="booking.create",
        entity_type="booking",
        entity_id=booking.id,
        user_id=user_id,
        details={
            "start": booking.start_at.isoformat(),
            "source": source,
            "google_event": booking.google_event_id,
        },
    )
    return booking, google


def reschedule(
    db: Session,
    booking_id: int,
    *,
    new_start: datetime,
    actor: str,
    user_id: int | None = None,
) -> tuple[Booking, GoogleResult]:
    """Переносит запись и двигает то же самое событие Google (без дубля)."""
    booking = db.get(Booking, booking_id)
    if booking is None or booking.status != BOOKED:
        raise booking_service.NotFoundError("Активная запись не найдена")
    old_start = booking.start_at

    busy = _without_own_event(
        _google_busy(db, booking.employee_id, new_start.astimezone(local_tz()).date()), booking
    )
    booking = booking_service.reschedule_booking(
        db, booking_id, new_start=new_start, busy_intervals=busy
    )
    google = _safe_push(db, booking, "reschedule", calendar_service.push_reschedule)
    log_action(
        db,
        actor=actor,
        action="booking.reschedule",
        entity_type="booking",
        entity_id=booking.id,
        user_id=user_id,
        details={"from": old_start.isoformat(), "to": booking.start_at.isoformat()},
    )
    return booking, google


def cancel(
    db: Session,
    booking_id: int,
    *,
    actor: str,
    user_id: int | None = None,
) -> tuple[Booking, GoogleResult]:
    """Отменяет запись и удаляет её событие из календаря."""
    booking = booking_service.set_booking_status(db, booking_id, CANCELLED)
    google = _safe_push(db, booking, "cancel", calendar_service.push_cancel)
    log_action(
        db,
        actor=actor,
        action="booking.cancelled",
        entity_type="booking",
        entity_id=booking.id,
        user_id=user_id,
        details={"start": booking.start_at.isoformat()},
    )
    return booking, google


def restore(
    db: Session,
    booking_id: int,
    *,
    actor: str,
    user_id: int | None = None,
) -> tuple[Booking, GoogleResult]:
    """Возвращает запись в работу с проверкой занятости.

    Отмена удалила событие Google — для отменённой записи заводим новое. Завершённая
    или «неявка» своё событие сохранили: его не считаем занятостью и не дублируем.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise booking_service.NotFoundError("Запись не найдена")
    was_cancelled = booking.status == CANCELLED
    busy = _google_busy(db, booking.employee_id, booking.start_at.astimezone(local_tz()).date())
    if not was_cancelled:
        busy = _without_own_event(busy, booking)
    booking = booking_service.restore_booking(db, booking_id, busy_intervals=busy)

    google: GoogleResult = None
    if was_cancelled:
        # Событие пишем в текущий рабочий календарь: его могли сменить, пока запись была отменена
        booking.google_event_id = None
        booking.calendar_id = None
        db.commit()
        google = _safe_push(db, booking, "restore", calendar_service.push_booking)
    log_action(
        db,
        actor=actor,
        action="booking.restored",
        entity_type="booking",
        entity_id=booking.id,
        user_id=user_id,
        details={"start": booking.start_at.isoformat(), "google_event": booking.google_event_id},
    )
    return booking, google


def set_status(
    db: Session,
    booking_id: int,
    status: str,
    *,
    actor: str,
    user_id: int | None = None,
) -> tuple[Booking, GoogleResult]:
    """Смена статуса. Отмена и возврат идут полным путём (Google + проверки), остальное — только БД."""
    if status == CANCELLED:
        return cancel(db, booking_id, actor=actor, user_id=user_id)
    if status == BOOKED:
        return restore(db, booking_id, actor=actor, user_id=user_id)
    booking = booking_service.set_booking_status(db, booking_id, status)
    log_action(
        db,
        actor=actor,
        action=f"booking.{status}",
        entity_type="booking",
        entity_id=booking.id,
        user_id=user_id,
    )
    return booking, None
