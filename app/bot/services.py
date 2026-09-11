"""Данные для бота: синхронные функции поверх booking_flow.

Хендлеры aiogram асинхронные, а сервисы и SQLAlchemy — синхронные, поэтому эти
функции вызываются в пуле потоков (app/bot/db.py). Наружу отдают простые словари:
ORM-объект после закрытия сессии становится непригоден.

Права клиента: он видит и меняет ТОЛЬКО свои записи — проверка владельца здесь,
booking_flow про клиентов ничего не знает.
"""

from datetime import date, datetime, time, timedelta

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import BOOKED, EXC_BLOCK, EXC_DAY_OFF, EXC_EXTRA, SOURCE_TELEGRAM
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.service import Service
from app.services import booking_flow, booking_service
from app.services.app_settings_service import COMPANY, get_setting
from app.services.schedule_service import local_now, local_tz

# Насколько вперёд клиенту разрешено записываться
BOOKING_HORIZON_DAYS = 90


class NotYours(Exception):
    """Запись принадлежит другому клиенту."""


# ==== Клиент ====

def get_or_create_client(db: Session, telegram_user_id: int, username: str | None, name: str) -> dict:
    client = db.scalar(select(Client).where(Client.telegram_user_id == telegram_user_id))
    if client is None:
        client = Client(telegram_user_id=telegram_user_id, telegram_username=username, name=name)
        db.add(client)
    else:
        client.telegram_username = username
        if not client.name:
            client.name = name
    db.commit()
    db.refresh(client)
    return {"id": client.id, "name": client.name, "phone": client.phone}


def set_phone(db: Session, client_id: int, phone: str) -> None:
    client = db.get(Client, client_id)
    if client:
        client.phone = phone
        db.commit()


# ==== Компания и справочники ====

def company_profile(db: Session) -> dict:
    return get_setting(db, COMPANY)


def price_label(price: Decimal | float | None, currency: str = "") -> str:
    """Цена для клиента: NULL — «по договорённости», 0 — «бесплатно»."""
    if price is None:
        return "по договорённости"
    if price == 0:
        return "бесплатно"
    amount = f"{float(price):,.0f}".replace(",", " ")
    return f"{amount} {currency}".strip()


def active_services(db: Session) -> list[dict]:
    rows = db.scalars(
        select(Service)
        .where(Service.is_active.is_(True), Service.archived_at.is_(None))
        .order_by(Service.sort_order, Service.name)
    ).all()
    currency = company_profile(db).get("currency") or ""
    return [
        {
            "id": s.id,
            "name": s.name,
            "duration": s.duration_minutes,
            "price": float(s.price) if s.price is not None else None,
            "price_label": price_label(s.price, currency),
            "description": s.description,
        }
        for s in rows
    ]


def employees_with_schedule(db: Session, service_id: int | None = None) -> list[dict]:
    """Сотрудники, к которым можно записаться (на услугу service_id, если задана).

    Нужен рабочий день в расписании или будущее дополнительное окно — иначе
    записаться не к кому. У сотрудника без привязанных услуг — все услуги.
    """
    rows = db.scalars(
        select(Employee)
        .options(selectinload(Employee.services))
        .where(Employee.is_active.is_(True), Employee.archived_at.is_(None))
        .order_by(Employee.name)
    ).all()
    today = local_now().date()
    result = []
    for e in rows:
        linked = [s.id for s in e.services]
        if service_id and linked and service_id not in linked:
            continue
        has_schedule = db.scalar(
            select(Schedule.id).where(
                Schedule.employee_id == e.id, Schedule.is_active.is_(True)
            ).limit(1)
        ) or db.scalar(
            select(ScheduleException.id).where(
                ScheduleException.employee_id == e.id,
                ScheduleException.kind == EXC_EXTRA,
                ScheduleException.date_to >= today,
            ).limit(1)
        )
        if has_schedule:
            result.append(
                {
                    "id": e.id,
                    "name": e.name,
                    "specialization": e.specialization,
                    "bio": e.bio,
                    "service_ids": linked,
                }
            )
    return result


def working_weekdays(db: Session, employee_id: int) -> set[int]:
    """Дни недели (0=Пн), в которые сотрудник вообще принимает."""
    rows = db.scalars(
        select(Schedule.weekday).where(
            Schedule.employee_id == employee_id, Schedule.is_active.is_(True)
        )
    ).all()
    return set(rows)


def open_days(db: Session, employee_id: int, first: date, last: date) -> set[date]:
    """Даты, которые можно нажать в календаре: рабочие дни с учётом исключений.

    Считается по расписанию, а не по свободным слотам — занятость проверяется
    уже после выбора дня, иначе календарь дёргал бы Google на каждую дату.
    """
    weekdays = working_weekdays(db, employee_id)
    exceptions = db.scalars(
        select(ScheduleException).where(
            ScheduleException.employee_id == employee_id,
            ScheduleException.date_to >= first,
            ScheduleException.date_from <= last,
        )
    ).all()
    days: set[date] = set()
    day = first
    while day <= last:
        covering = [x for x in exceptions if x.date_from <= day <= x.date_to]
        closed = any(
            x.kind == EXC_DAY_OFF or (x.kind == EXC_BLOCK and not (x.start_time and x.end_time))
            for x in covering
        )
        extra = any(x.kind == EXC_EXTRA and x.start_time and x.end_time for x in covering)
        if not closed and (day.weekday() in weekdays or extra):
            days.add(day)
        day += timedelta(days=1)
    return days


def free_times(
    db: Session, employee_id: int, service_id: int, day: date, exclude_booking_id: int | None = None
) -> list[str]:
    """Свободные слоты как «HH:MM» — учитывают расписание, брони и занятость Google."""
    slots = booking_flow.available_slots(db, employee_id, service_id, day, exclude_booking_id)
    return [s.strftime("%H:%M") for s in slots]


# ==== Записи ====

def _to_local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def _card(booking: Booking) -> dict:
    start = _to_local(booking.start_at)
    return {
        "id": booking.id,
        "service": booking.service.name,
        "employee": booking.employee.name,
        "date": start.date(),
        "start": start.strftime("%H:%M"),
        "end": _to_local(booking.end_at).strftime("%H:%M"),
        "status": booking.status,
    }


def parse_slot(day: date, slot: str) -> datetime:
    hh, mm = slot.split(":")
    return datetime.combine(day, time(int(hh), int(mm)), tzinfo=local_tz())


def create(
    db: Session, *, client_id: int, employee_id: int, service_id: int, day: date, slot: str
) -> dict:
    """Создаёт запись. SlotTakenError пробрасывается — хендлер попросит выбрать другое время."""
    booking, _google = booking_flow.create(
        db,
        client_id=client_id,
        employee_id=employee_id,
        service_id=service_id,
        start_at=parse_slot(day, slot),
        source=SOURCE_TELEGRAM,
        actor=f"tg:{client_id}",
    )
    return _card(booking)


def my_bookings(db: Session, client_id: int) -> list[dict]:
    """Активные записи клиента начиная с текущего момента."""
    rows = db.scalars(
        select(Booking)
        .where(
            Booking.client_id == client_id,
            Booking.status == BOOKED,
            Booking.start_at >= local_now(),
        )
        .order_by(Booking.start_at)
    ).all()
    return [_card(b) for b in rows]


def _owned(db: Session, booking_id: int, client_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise booking_service.NotFoundError("Запись не найдена")
    if booking.client_id != client_id:
        raise NotYours("Это чужая запись")
    return booking


def booking_card(db: Session, booking_id: int, client_id: int) -> dict:
    return _card(_owned(db, booking_id, client_id))


def booking_context(db: Session, booking_id: int, client_id: int) -> dict:
    """Данные, нужные для подбора нового времени при переносе."""
    booking = _owned(db, booking_id, client_id)
    return {
        "id": booking.id,
        "employee_id": booking.employee_id,
        "service_id": booking.service_id,
        "service": booking.service.name,
        "employee": booking.employee.name,
        "date": _to_local(booking.start_at).date(),
        "start": _to_local(booking.start_at).strftime("%H:%M"),
    }


def reschedule(db: Session, booking_id: int, client_id: int, day: date, slot: str) -> dict:
    _owned(db, booking_id, client_id)
    booking, _google = booking_flow.reschedule(
        db, booking_id, new_start=parse_slot(day, slot), actor=f"tg:{client_id}"
    )
    return _card(booking)


def cancel(db: Session, booking_id: int, client_id: int) -> dict:
    _owned(db, booking_id, client_id)
    booking, _google = booking_flow.cancel(db, booking_id, actor=f"tg:{client_id}")
    return _card(booking)


def horizon() -> tuple[date, date]:
    """Границы, внутри которых клиент может выбирать дату."""
    today = local_now().date()
    return today, today + timedelta(days=BOOKING_HORIZON_DAYS)
