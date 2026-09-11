"""Возврат записи в «Активна»: проверка занятости и событие Google."""

from datetime import datetime, time

import pytest

from app.models.enums import BOOKED, CANCELLED, COMPLETED, SOURCE_ADMIN
from app.services import booking_flow, booking_service
from app.services.schedule_service import local_tz


def _create(db, client, employee, service, workday, hh=10):
    booking, _google = booking_flow.create(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=datetime.combine(workday, time(hh, 0), tzinfo=local_tz()),
        source=SOURCE_ADMIN,
        actor="test",
    )
    return booking


def test_отменённая_запись_возвращается(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday)
    booking_flow.cancel(db, booking.id, actor="test")

    restored, google = booking_flow.set_status(db, booking.id, BOOKED, actor="test")

    assert restored.status == BOOKED
    assert google is None  # календарь не подключён — событие и не ожидалось


def test_вернуть_на_занятое_время_нельзя(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday)
    booking_flow.cancel(db, booking.id, actor="test")
    _create(db, client, employee, service, workday)  # то же время заняли

    with pytest.raises(booking_service.SlotTakenError):
        booking_flow.set_status(db, booking.id, BOOKED, actor="test")
    db.refresh(booking)
    assert booking.status == CANCELLED


def test_отменённой_записи_заводится_новое_событие(db, client, employee, service, workday, monkeypatch):
    booking = _create(db, client, employee, service, workday)
    booking_flow.cancel(db, booking.id, actor="test")
    pushed: list[int] = []
    monkeypatch.setattr(
        booking_flow.calendar_service, "push_booking", lambda _db, b: pushed.append(b.id) or "evt-new"
    )

    _restored, google = booking_flow.set_status(db, booking.id, BOOKED, actor="test")

    assert pushed == [booking.id]
    assert google is True


def test_завершённая_возвращается_без_дубля_события(db, client, employee, service, workday, monkeypatch):
    booking = _create(db, client, employee, service, workday)
    booking_flow.set_status(db, booking.id, COMPLETED, actor="test")
    pushed: list[int] = []
    monkeypatch.setattr(
        booking_flow.calendar_service, "push_booking", lambda _db, b: pushed.append(b.id)
    )

    restored, _google = booking_flow.set_status(db, booking.id, BOOKED, actor="test")

    assert restored.status == BOOKED
    assert pushed == []
