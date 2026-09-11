"""Расписание: рабочие интервалы и вычисление свободных слотов."""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.booking import Booking
from app.models.enums import BOOKED, EXC_BLOCK, EXC_DAY_OFF, EXC_EXTRA
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException

logger = logging.getLogger(__name__)

SLOT_STEP_MINUTES = 30
WEEKDAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def local_now() -> datetime:
    return datetime.now(local_tz())


def _combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=local_tz())


Interval = tuple[datetime, datetime]


def _subtract(intervals: list[Interval], cut_start: datetime, cut_end: datetime) -> list[Interval]:
    """Вырезает отрезок из списка интервалов (перерыв, закрытое время)."""
    result: list[Interval] = []
    for s, e in intervals:
        if cut_end <= s or cut_start >= e:
            result.append((s, e))
            continue
        if s < cut_start:
            result.append((s, cut_start))
        if cut_end < e:
            result.append((cut_end, e))
    return result


def _merge(intervals: list[Interval]) -> list[Interval]:
    """Склеивает пересекающиеся интервалы — дополнительное окно может задеть рабочее время."""
    merged: list[Interval] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def exceptions_on(db: Session, employee_id: int, day: date) -> list[ScheduleException]:
    return list(
        db.scalars(
            select(ScheduleException).where(
                ScheduleException.employee_id == employee_id,
                ScheduleException.date_from <= day,
                ScheduleException.date_to >= day,
            )
        )
    )


def _has_times(exc: ScheduleException) -> bool:
    return bool(exc.start_time and exc.end_time and exc.end_time > exc.start_time)


def working_intervals(db: Session, employee_id: int, day: date) -> list[Interval]:
    """Рабочие интервалы сотрудника на дату (в локальной таймзоне).

    Недельное расписание минус перерыв, поверх — исключения на эту дату:
    выходной убирает день целиком, дополнительное окно добавляет время,
    закрытое время вырезается.
    """
    intervals: list[Interval] = []
    row = db.scalar(
        select(Schedule).where(
            Schedule.employee_id == employee_id,
            Schedule.weekday == day.weekday(),
            Schedule.is_active.is_(True),
        )
    )
    if row is not None and row.end_time > row.start_time:
        intervals = [(_combine(day, row.start_time), _combine(day, row.end_time))]
        if row.break_start and row.break_end and row.break_end > row.break_start:
            intervals = _subtract(intervals, _combine(day, row.break_start), _combine(day, row.break_end))

    exceptions = exceptions_on(db, employee_id, day)
    # «Закрыть время» без часов — то же, что выходной
    if any(e.kind == EXC_DAY_OFF or (e.kind == EXC_BLOCK and not _has_times(e)) for e in exceptions):
        return []
    extra = [
        (_combine(day, e.start_time), _combine(day, e.end_time))
        for e in exceptions
        if e.kind == EXC_EXTRA and _has_times(e)
    ]
    if extra:
        intervals = _merge(intervals + extra)
    for e in exceptions:
        if e.kind == EXC_BLOCK:
            intervals = _subtract(intervals, _combine(day, e.start_time), _combine(day, e.end_time))
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
