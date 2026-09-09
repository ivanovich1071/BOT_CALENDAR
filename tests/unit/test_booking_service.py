"""Ядро бронирования: пересечения, перенос, статусы."""

from datetime import datetime, time, timedelta, timezone

import pytest

from app.models.enums import BOOKED, CANCELLED, SOURCE_ADMIN
from app.services import booking_service
from app.services.schedule_service import local_tz


def _at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=local_tz())


def _create(db, client, employee, service, day, hh, mm=0, **kwargs):
    return booking_service.create_booking(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=_at(day, hh, mm),
        source=SOURCE_ADMIN,
        **kwargs,
    )


def test_запись_создаётся_и_хранится_в_utc(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, 10)
    assert booking.status == BOOKED
    assert booking.start_at.tzinfo is not None
    assert booking.end_at - booking.start_at == timedelta(minutes=service.duration_minutes)
    assert booking.start_at.astimezone(local_tz()).hour == 10


def test_наивное_время_отклоняется(db, client, employee, service, workday):
    with pytest.raises(booking_service.BookingError):
        booking_service.create_booking(
            db,
            client_id=client.id,
            employee_id=employee.id,
            service_id=service.id,
            start_at=datetime.combine(workday, time(10, 0)),  # без таймзоны
            source=SOURCE_ADMIN,
        )


def test_двойная_бронь_на_тот_же_слот_отклоняется(db, client, employee, service, workday):
    _create(db, client, employee, service, workday, 10)
    with pytest.raises(booking_service.SlotTakenError):
        _create(db, client, employee, service, workday, 10)


def test_частичное_пересечение_тоже_отклоняется(db, client, employee, service, workday):
    _create(db, client, employee, service, workday, 10)
    with pytest.raises(booking_service.SlotTakenError):
        _create(db, client, employee, service, workday, 10, 30)


def test_занятость_google_блокирует_создание(db, client, employee, service, workday):
    busy_start = _at(workday, 15).astimezone(timezone.utc)
    with pytest.raises(booking_service.SlotTakenError):
        _create(
            db, client, employee, service, workday, 15,
            busy_intervals=[(busy_start, busy_start + timedelta(hours=1))],
        )


def test_отменённая_запись_освобождает_слот(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, 10)
    booking_service.set_booking_status(db, booking.id, CANCELLED)
    again = _create(db, client, employee, service, workday, 10)
    assert again.id != booking.id


def test_перенос_не_конфликтует_сам_с_собой(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, 10)
    moved = booking_service.reschedule_booking(db, booking.id, new_start=_at(workday, 10, 30))
    assert moved.start_at.astimezone(local_tz()).strftime("%H:%M") == "10:30"
    assert moved.end_at - moved.start_at == timedelta(minutes=60)


def test_перенос_на_занятое_время_отклоняется(db, client, employee, service, workday):
    first = _create(db, client, employee, service, workday, 10)
    second = _create(db, client, employee, service, workday, 12)
    with pytest.raises(booking_service.SlotTakenError):
        booking_service.reschedule_booking(db, second.id, new_start=_at(workday, 10))
    assert db.get(type(first), first.id).start_at == first.start_at


def test_недопустимый_статус(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, 10)
    with pytest.raises(booking_service.BookingError):
        booking_service.set_booking_status(db, booking.id, "неизвестно")
