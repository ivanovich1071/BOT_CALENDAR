"""Календарь админки: раскладка записей по сетке времени.

Здесь только расчёт — позиции в минутах, HTML собирают шаблоны admin/templates/calendar.
Записи берутся из БД. Google добавляет лишь «чужие» события рабочего календаря
сотрудника: так видно, почему это время не предлагается клиентам.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.booking import Booking
from app.models.employee import Employee
from app.models.enums import BOOKED, CANCELLED, EXC_BLOCK, EXC_DAY_OFF
from app.services import calendar_service
from app.services.app_settings_service import BOOKING, get_setting
from app.services.schedule_service import (
    SLOT_STEP_MINUTES,
    _subtract,
    exceptions_on,
    local_now,
    local_tz,
    working_intervals,
)

logger = logging.getLogger(__name__)

# Шкала дня, когда ни у кого нет ни рабочего времени, ни записей
DEFAULT_START_MINUTE = 9 * 60
DEFAULT_END_MINUTE = 18 * 60
DAY_MINUTES = 24 * 60

WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

Interval = tuple[datetime, datetime]


@dataclass
class Span:
    top: int  # минут от начала шкалы
    height: int


@dataclass
class Block:
    booking: Booking
    top: int
    height: int
    lane: int = 0
    lanes: int = 1


@dataclass
class FreeCell:
    top: int
    height: int
    slot: str  # «HH:MM» — подставляется в форму новой записи


@dataclass
class Column:
    employee: Employee
    day: date
    working: list[Span] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    free: list[FreeCell] = field(default_factory=list)
    google_busy: list[Span] = field(default_factory=list)
    day_off: str | None = None
    now_top: int | None = None


@dataclass
class TimeGrid:
    start_minute: int
    end_minute: int
    columns: list[Column]

    @property
    def minutes(self) -> int:
        return self.end_minute - self.start_minute

    @property
    def hours(self) -> list[tuple[int, str]]:
        return [
            (m - self.start_minute, f"{m // 60:02d}:00")
            for m in range(self.start_minute, self.end_minute + 1, 60)
        ]


@dataclass
class Cell:
    """Ячейка «сотрудник × день» в неделе всех."""

    day: date
    bookings: list[Booking]
    day_off: str | None


@dataclass
class MonthDay:
    day: date
    in_month: bool
    counts: Counter

    @property
    def active(self) -> int:
        return self.counts.get(BOOKED, 0)


# ==== Даты и заголовки ====

def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def day_title(day: date) -> str:
    return f"{WEEKDAY_FULL[day.weekday()]}, {day.day} {MONTHS_GEN[day.month - 1]} {day.year}"


def week_title(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTHS_GEN[sunday.month - 1]} {sunday.year}"
    return f"{monday.day} {MONTHS_GEN[monday.month - 1]} – {sunday.day} {MONTHS_GEN[sunday.month - 1]} {sunday.year}"


def month_title(day: date) -> str:
    return f"{MONTHS_NOM[day.month - 1]} {day.year}"


def _bounds_utc(first: date, last: date) -> Interval:
    """Локальные сутки с first по last включительно — в UTC."""
    tz = local_tz()
    start = datetime.combine(first, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


def _minute(dt: datetime, day: date) -> int:
    """Минута от начала локальных суток day; у записи через полночь выходит за 0…1440."""
    midnight = datetime.combine(day, time.min, tzinfo=local_tz())
    return int((dt.astimezone(local_tz()) - midnight).total_seconds() // 60)


def _local_day(dt: datetime) -> date:
    return dt.astimezone(local_tz()).date()


# ==== Данные ====

def load_bookings(
    db: Session, first: date, last: date, employee_ids: list[int], *, show_cancelled: bool = False
) -> list[Booking]:
    if not employee_ids:
        return []
    start_utc, end_utc = _bounds_utc(first, last)
    q = (
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.service), selectinload(Booking.employee))
        .where(Booking.employee_id.in_(employee_ids), Booking.start_at < end_utc, Booking.end_at > start_utc)
        .order_by(Booking.start_at)
    )
    if not show_cancelled:
        q = q.where(Booking.status != CANCELLED)
    return list(db.scalars(q).all())


def foreign_google_busy(
    db: Session, employee_id: int, first: date, last: date, bookings: list[Booking]
) -> list[Interval]:
    """Занятость рабочего Google-календаря без событий наших же записей."""
    if not calendar_service.google_enabled(db):
        return []
    start_utc, end_utc = _bounds_utc(first, last)
    try:
        busy = calendar_service.get_busy_intervals(db, employee_id, start_utc, end_utc)
    except Exception:  # noqa: BLE001 — календарь показывается и без Google
        logger.exception("Не удалось получить занятость Google (сотрудник #%s)", employee_id)
        return []
    # Вычитаем, а не сравниваем на равенство: Google склеивает соседние события в один интервал
    for b in bookings:
        if b.employee_id == employee_id and b.status != CANCELLED:
            busy = _subtract(busy, calendar_service.as_utc(b.start_at), calendar_service.as_utc(b.end_at))
    return [(s, e) for s, e in busy if e > s]


def _step(db: Session) -> int:
    return max(5, int(get_setting(db, BOOKING).get("slot_step_minutes") or SLOT_STEP_MINUTES))


def _day_off_label(db: Session, employee_id: int, day: date) -> str:
    for exc in exceptions_on(db, employee_id, day):
        if exc.kind == EXC_DAY_OFF or (exc.kind == EXC_BLOCK and not (exc.start_time and exc.end_time)):
            return exc.note or "Выходной"
    return "Не работает"


def _assign_lanes(blocks: list[Block]) -> None:
    """Пересекающиеся записи встают рядом, а не друг на друга."""
    lane_ends: list[int] = []
    for block in sorted(blocks, key=lambda b: (b.top, -b.height)):
        for i, end in enumerate(lane_ends):
            if end <= block.top:
                block.lane, lane_ends[i] = i, block.top + block.height
                break
        else:
            block.lane = len(lane_ends)
            lane_ends.append(block.top + block.height)
    for block in blocks:
        block.lanes = max(1, len(lane_ends))


def build_column(
    db: Session,
    employee: Employee,
    day: date,
    bookings: list[Booking],
    busy: list[Interval],
    *,
    step: int,
    now: datetime,
) -> Column:
    """Колонка одного сотрудника за один день; позиции — минуты от начала суток."""
    column = Column(employee=employee, day=day)
    intervals = working_intervals(db, employee.id, day)
    column.working = [Span(_minute(s, day), _minute(e, day) - _minute(s, day)) for s, e in intervals]
    if not intervals:
        column.day_off = _day_off_label(db, employee.id, day)

    own = [b for b in bookings if b.employee_id == employee.id and _local_day(b.start_at) == day]
    column.blocks = [
        Block(b, _minute(b.start_at, day), max(1, _minute(b.end_at, day) - _minute(b.start_at, day)))
        for b in own
    ]
    _assign_lanes(column.blocks)

    day_start, day_end = _bounds_utc(day, day)
    day_busy = [(max(s, day_start), min(e, day_end)) for s, e in busy if s < day_end and e > day_start]
    column.google_busy = [Span(_minute(s, day), _minute(e, day) - _minute(s, day)) for s, e in day_busy]

    # Свободная ячейка — рабочее время без записей и без чужих событий Google, не в прошлом
    taken = [(b.start_at, b.end_at) for b in own if b.status != CANCELLED] + day_busy
    for s, e in intervals:
        slot = s
        while slot < e:
            cell_end = min(slot + timedelta(minutes=step), e)
            if slot >= now and not any(slot < te and ts < cell_end for ts, te in taken):
                column.free.append(
                    FreeCell(_minute(slot, day), _minute(cell_end, day) - _minute(slot, day), slot.strftime("%H:%M"))
                )
            slot += timedelta(minutes=step)

    if day == now.date():
        column.now_top = _minute(now, day)
    return column


def _finalize(columns: list[Column]) -> TimeGrid:
    """Шкала от самого раннего до самого позднего часа работы или записи, по целым часам."""
    edges = [(s.top, s.top + s.height) for c in columns for s in c.working]
    edges += [(b.top, b.top + b.height) for c in columns for b in c.blocks]
    if edges:
        start = max(0, min(a for a, _ in edges) // 60 * 60)
        end = min(DAY_MINUTES, -(-max(b for _, b in edges) // 60) * 60)
    else:
        start, end = DEFAULT_START_MINUTE, DEFAULT_END_MINUTE
    end = max(end, min(DAY_MINUTES, start + 60))

    def shift(top: int, height: int) -> tuple[int, int]:
        a, b = max(top, start), min(top + height, end)
        return a - start, max(0, b - a)

    for c in columns:
        c.working = [Span(*shift(s.top, s.height)) for s in c.working]
        c.google_busy = [sp for sp in (Span(*shift(s.top, s.height)) for s in c.google_busy) if sp.height > 0]
        c.free = [FreeCell(*shift(f.top, f.height), f.slot) for f in c.free]
        for b in c.blocks:
            b.top, b.height = shift(b.top, b.height)
        if c.now_top is not None:
            c.now_top = c.now_top - start if start <= c.now_top <= end else None
    return TimeGrid(start, end, columns)


# ==== Виды ====

def day_view(db: Session, day: date, employees: list[Employee], *, show_cancelled: bool = False) -> TimeGrid:
    """День: сотрудники колонками."""
    bookings = load_bookings(db, day, day, [e.id for e in employees], show_cancelled=show_cancelled)
    step, now = _step(db), local_now()
    return _finalize([
        build_column(db, e, day, bookings, foreign_google_busy(db, e.id, day, day, bookings), step=step, now=now)
        for e in employees
    ])


def week_view(db: Session, day: date, employee: Employee, *, show_cancelled: bool = False) -> TimeGrid:
    """Неделя одного сотрудника: дни колонками. Google — один запрос на всю неделю."""
    monday = monday_of(day)
    sunday = monday + timedelta(days=6)
    bookings = load_bookings(db, monday, sunday, [employee.id], show_cancelled=show_cancelled)
    busy = foreign_google_busy(db, employee.id, monday, sunday, bookings)
    step, now = _step(db), local_now()
    return _finalize([
        build_column(db, employee, monday + timedelta(days=i), bookings, busy, step=step, now=now)
        for i in range(7)
    ])


def week_all_view(
    db: Session, day: date, employees: list[Employee], *, show_cancelled: bool = False
) -> list[tuple[Employee, list[Cell]]]:
    """Неделя всех: строки — сотрудники, столбцы — дни."""
    monday = monday_of(day)
    bookings = load_bookings(
        db, monday, monday + timedelta(days=6), [e.id for e in employees], show_cancelled=show_cancelled
    )
    rows = []
    for e in employees:
        cells = []
        for i in range(7):
            d = monday + timedelta(days=i)
            own = [b for b in bookings if b.employee_id == e.id and _local_day(b.start_at) == d]
            off = None if working_intervals(db, e.id, d) else _day_off_label(db, e.id, d)
            cells.append(Cell(d, own, off))
        rows.append((e, cells))
    return rows


def month_view(db: Session, day: date, employees: list[Employee]) -> list[list[MonthDay]]:
    """Месяц: недели по 7 дней, в каждом дне — число записей по статусам."""
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    start, end = monday_of(first), monday_of(last) + timedelta(days=6)
    counts: dict[date, Counter] = {}
    for b in load_bookings(db, start, end, [e.id for e in employees], show_cancelled=True):
        counts.setdefault(_local_day(b.start_at), Counter())[b.status] += 1
    weeks = []
    current = start
    while current <= end:
        days = [current + timedelta(days=i) for i in range(7)]
        weeks.append([MonthDay(d, d.month == first.month, counts.get(d, Counter())) for d in days])
        current += timedelta(days=7)
    return weeks
