"""Бизнес-логика бронирования: создание/перенос/отмена.

Защита от двойной записи: Redis-lock на слот + проверка пересечений в БД,
опциональная проверка занятости в Google Calendar (busy_intervals от CalendarService).
Интеграция с Google вызывается отдельным слоем (hooks) — ядро остаётся чистым.
"""

import logging
from datetime import datetime, timedelta, timezone

import redis as redis_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.booking import Booking
from app.models.employee import Employee
from app.models.enums import BOOKED, CANCELLED, COMPLETED, NO_SHOW
from app.models.service import Service

logger = logging.getLogger(__name__)

_redis_client: redis_lib.Redis | None = None


def _redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        )
    return _redis_client


class SlotTakenError(Exception):
    """Слот уже занят (в БД или в Google)."""


class NotFoundError(Exception):
    pass


class BookingError(Exception):
    pass


def _lock_key(employee_id: int, start: datetime) -> str:
    return f"slotlock:{employee_id}:{start.isoformat()}"


def _db_overlap(
    db: Session,
    employee_id: int,
    start: datetime,
    end: datetime,
    exclude_booking_id: int | None = None,
) -> bool:
    q = select(Booking.id).where(
        Booking.employee_id == employee_id,
        Booking.status == BOOKED,
        Booking.start_at < end,
        Booking.end_at > start,
    )
    if exclude_booking_id:
        q = q.where(Booking.id != exclude_booking_id)
    return db.scalar(q.limit(1)) is not None


def validate_refs(db: Session, client_id: int, employee_id: int, service_id: int) -> tuple:
    service = db.get(Service, service_id)
    employee = db.get(Employee, employee_id)
    if service is None or not service.is_active or service.archived_at is not None:
        raise NotFoundError("Услуга не найдена")
    if employee is None or not employee.is_active or employee.archived_at is not None:
        raise NotFoundError("Сотрудник не найден")
    from app.models.client import Client

    client = db.get(Client, client_id)
    if client is None:
        raise NotFoundError("Клиент не найден")
    return client, employee, service


def create_booking(
    db: Session,
    *,
    client_id: int,
    employee_id: int,
    service_id: int,
    start_at: datetime,
    source: str,
    notes: str | None = None,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
    calendar_id: int | None = None,
) -> Booking:
    """Создаёт запись. Бросает SlotTakenError/NotFoundError.

    start_at — tz-aware datetime (UTC или с таймзоной; приводится к UTC).
    """
    client, employee, service = validate_refs(db, client_id, employee_id, service_id)
    if start_at.tzinfo is None:
        raise BookingError("start_at должен быть tz-aware")
    start_utc = start_at.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=service.duration_minutes)

    lock = None
    try:
        lock = _redis().lock(
            _lock_key(employee_id, start_utc), timeout=15, blocking_timeout=5
        )
        lock.acquire()
    except Exception:  # noqa: BLE001 — Redis недоступен: работаем на проверке БД
        logger.warning("Redis-lock недоступен, слот %s/%s без блокировки", employee_id, start_utc)
        lock = None

    try:
        if _db_overlap(db, employee_id, start_utc, end_utc):
            raise SlotTakenError("Время уже занято")
        for bs, be in busy_intervals or []:
            if start_utc < be and bs < end_utc:
                raise SlotTakenError("Время занято в Google Calendar")

        booking = Booking(
            client_id=client.id,
            employee_id=employee.id,
            service_id=service.id,
            calendar_id=calendar_id,
            start_at=start_utc,
            end_at=end_utc,
            source=source,
            notes=notes,
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)
        return booking
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001
                pass


def reschedule_booking(
    db: Session,
    booking_id: int,
    *,
    new_start: datetime,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None or booking.status != BOOKED:
        raise NotFoundError("Активная запись не найдена")
    if new_start.tzinfo is None:
        raise BookingError("new_start должен быть tz-aware")
    duration = booking.end_at - booking.start_at
    new_start_utc = new_start.astimezone(timezone.utc)
    new_end_utc = new_start_utc + duration

    lock = None
    try:
        lock = _redis().lock(
            _lock_key(booking.employee_id, new_start_utc), timeout=15, blocking_timeout=5
        )
        lock.acquire()
    except Exception:  # noqa: BLE001
        logger.warning("Redis-lock недоступен при переносе записи #%s", booking_id)
        lock = None
    try:
        if _db_overlap(db, booking.employee_id, new_start_utc, new_end_utc, exclude_booking_id=booking.id):
            raise SlotTakenError("Время уже занято")
        for bs, be in busy_intervals or []:
            if new_start_utc < be and bs < new_end_utc:
                raise SlotTakenError("Время занято в Google Calendar")
        booking.start_at = new_start_utc
        booking.end_at = new_end_utc
        db.commit()
        db.refresh(booking)
        return booking
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001
                pass


def ensure_free(
    db: Session,
    *,
    employee_id: int,
    start_utc: datetime,
    end_utc: datetime,
    exclude_booking_id: int | None = None,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
) -> None:
    """Бросает SlotTakenError, если время пересекается с записью в БД или занятостью Google."""
    if _db_overlap(db, employee_id, start_utc, end_utc, exclude_booking_id=exclude_booking_id):
        raise SlotTakenError("Время уже занято")
    for bs, be in busy_intervals or []:
        if start_utc < be and bs < end_utc:
            raise SlotTakenError("Время занято в Google Calendar")


def update_booking(
    db: Session,
    booking_id: int,
    *,
    client_id: int,
    employee_id: int,
    service_id: int,
    start_at: datetime,
    notes: str | None,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
) -> Booking:
    """Правка записи целиком. Время проверяется, только если запись активна:
    отменённая или завершённая никого не вытесняет."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise NotFoundError("Запись не найдена")
    if start_at.tzinfo is None:
        raise BookingError("start_at должен быть tz-aware")
    client, employee, service = validate_refs(db, client_id, employee_id, service_id)
    start_utc = start_at.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=service.duration_minutes)

    lock = None
    if booking.status == BOOKED:
        try:
            lock = _redis().lock(_lock_key(employee.id, start_utc), timeout=15, blocking_timeout=5)
            lock.acquire()
        except Exception:  # noqa: BLE001 — Redis недоступен: работаем на проверке БД
            logger.warning("Redis-lock недоступен при правке записи #%s", booking_id)
            lock = None
    try:
        if booking.status == BOOKED:
            ensure_free(
                db,
                employee_id=employee.id,
                start_utc=start_utc,
                end_utc=end_utc,
                exclude_booking_id=booking.id,
                busy_intervals=busy_intervals,
            )
        booking.client_id = client.id
        booking.employee_id = employee.id
        booking.service_id = service.id
        booking.start_at = start_utc
        booking.end_at = end_utc
        booking.notes = notes
        db.commit()
        db.refresh(booking)
        return booking
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001
                pass


def restore_booking(
    db: Session,
    booking_id: int,
    *,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
) -> Booking:
    """Возвращает запись в «Активна». Пока она была отменена, время могли занять."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise NotFoundError("Запись не найдена")
    if booking.status == BOOKED:
        return booking
    if _db_overlap(db, booking.employee_id, booking.start_at, booking.end_at, exclude_booking_id=booking.id):
        raise SlotTakenError("Время уже занято")
    for bs, be in busy_intervals or []:
        if booking.start_at < be and bs < booking.end_at:
            raise SlotTakenError("Время занято в Google Calendar")
    booking.status = BOOKED
    db.commit()
    db.refresh(booking)
    return booking


def set_booking_status(db: Session, booking_id: int, status: str) -> Booking:
    if status not in (BOOKED, CANCELLED, COMPLETED, NO_SHOW):
        raise BookingError("Недопустимый статус")
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise NotFoundError("Запись не найдена")
    booking.status = status
    db.commit()
    db.refresh(booking)
    return booking
