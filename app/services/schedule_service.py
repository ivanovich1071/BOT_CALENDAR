"""Расписание: рабочие интервалы и вычисление свободных слотов."""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.booking import Booking
from app.models.enums import BOOKED
from app.models.schedule import Schedule

logger = logging.getLogger(__name__)

SLOT_STEP_MINUTES = 30
WEEKDAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def local_now() -> datetime:
    return datetime.now(local_tz())


def _combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=local_tz())


def working_intervals(db: Session, employee_id: int, day: date) -> list[tuple[datetime, datetime]]:
    """Рабочие интервалы сотрудника на дату (в локальной таймзоне), минус перерыв."""
    row = db.scalar(
        select(Schedule).where(
            Schedule.employee_id == employee_id,
            Schedule.weekday == day.weekday(),
            Schedule.is_active.is_(True),
        )
    )
    if row is None or row.end_time <= row.start_time:
        return []
    intervals = [(_combine(day, row.start_time), _combine(day, row.end_time))]
    if row.break_start and row.break_end and row.break_end > row.break_start:
        b_start, b_end = _combine(day, row.break_start), _combine(day, row.break_end)
        split: list[tuple[datetime, datetime]] = []
        for s, e in intervals:
            if b_end <= s or b_start >= e:
                split.append((s, e))
                continue
            if s < b_start:
                split.append((s, b_start))
            if b_end < e:
                split.append((b_end, e))
        intervals = split
    return intervals


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def free_slots(
    db: Session,
    employee_id: int,
    duration_minutes: int,
    day: date,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    """Свободные слоты: рабочее расписание − занятое в БД − переданные busy (Google).

    Возвращает список локальных (tz-aware) datetime — начала слотов.
    """
    busy = list(busy_intervals or [])
    # Брони этого дня из БД (границы дня в локальной таймзоне → UTC)
    day_start_utc = _combine(day, time.min).astimezone(ZoneInfo("UTC"))
    day_end_utc = _combine(day, time.max).astimezone(ZoneInfo("UTC"))
    q = (
        select(Booking)
        .where(
            Booking.employee_id == employee_id,
            Booking.status == BOOKED,
            Booking.start_at < day_end_utc,
            Booking.end_at > day_start_utc,
        )
    )
    if exclude_booking_id:
        q = q.where(Booking.id != exclude_booking_id)
    for b in db.scalars(q):
        busy.append((b.start_at, b.end_at))

    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=SLOT_STEP_MINUTES)
    now = local_now()
    slots: list[datetime] = []
    for s, e in working_intervals(db, employee_id, day):
        slot = s
        while slot + duration <= e:
            if slot >= now and not any(
                _overlaps(slot, slot + duration, bs, be) for bs, be in busy
            ):
                slots.append(slot)
            slot += step
    return slots
