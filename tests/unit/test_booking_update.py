"""Правка записи из админки: время, услуга, сотрудник, заметка и событие Google."""

from datetime import datetime, time

import pytest

from app.models.employee import Employee
from app.models.enums import CANCELLED, SOURCE_ADMIN
from app.models.schedule import Schedule
from app.models.service import Service
from app.services import booking_flow, booking_service
from app.services.schedule_service import local_tz


def _at(day, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=local_tz())


def _create(db, client, employee, service, day, hh=10):
    booking, _google = booking_flow.create(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=_at(day, hh),
        source=SOURCE_ADMIN,
        actor="test",
    )
    return booking


def _update(db, booking, **changes):
    fields = {
        "client_id": booking.client_id,
        "employee_id": booking.employee_id,
        "service_id": booking.service_id,
        "start_at": booking.start_at,
        "notes": booking.notes,
    }
    fields.update(changes)
    return booking_flow.update(db, booking.id, actor="test", **fields)


@pytest.fixture
def second_employee(db):
    row = Employee(name="Петрова")
    db.add(row)
    db.commit()
    for weekday in range(5):
        db.add(Schedule(employee_id=row.id, weekday=weekday, start_time=time(9), end_time=time(18)))
    db.commit()
    return row


def test_время_и_заметка_меняются(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday)

    updated, google = _update(db, booking, start_at=_at(workday, 15), notes="Перенесли по звонку")

    assert updated.start_at.astimezone(local_tz()).strftime("%H:%M") == "15:00"
    assert updated.notes == "Перенесли по звонку"
    assert google is None  # календарь не подключён


def test_смена_услуги_пересчитывает_конец(db, client, employee, service, workday):
    long = Service(name="Лаборатория", duration_minutes=120)
    db.add(long)
    db.commit()
    booking = _create(db, client, employee, service, workday)

    updated, _google = _update(db, booking, service_id=long.id)

    assert (updated.end_at - updated.start_at).total_seconds() == 2 * 3600


def test_занятое_время_не_сохраняется(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, hh=10)
    _create(db, client, employee, service, workday, hh=12)

    with pytest.raises(booking_service.SlotTakenError):
        _update(db, booking, start_at=_at(workday, 12))

    db.refresh(booking)
    assert booking.start_at.astimezone(local_tz()).hour == 10


def test_смена_сотрудника_переносит_событие(db, client, employee, second_employee, service, workday, monkeypatch):
    booking = _create(db, client, employee, service, workday)
    booking.google_event_id = "evt-old"
    db.commit()
    removed: list[str] = []
    created: list[tuple] = []

    def fake_create(_db, b):
        created.append((b.employee_id, b.google_event_id, b.calendar_id))
        b.google_event_id = "evt-new"
        _db.commit()
        return "evt-new"

    monkeypatch.setattr(booking_flow.calendar_service, "push_cancel", lambda _db, b: removed.append(b.google_event_id) or True)
    monkeypatch.setattr(booking_flow.calendar_service, "push_booking", fake_create)

    updated, google = _update(db, booking, employee_id=second_employee.id)

    assert removed == ["evt-old"]
    assert created == [(second_employee.id, None, None)]
    assert updated.google_event_id == "evt-new" and google is True


def test_на_занятое_время_у_другого_сотрудника_событие_не_трогается(
    db, client, employee, second_employee, service, workday, monkeypatch
):
    booking = _create(db, client, employee, service, workday, hh=10)
    booking.google_event_id = "evt-old"
    db.commit()
    _create(db, client, second_employee, service, workday, hh=10)
    removed: list[str] = []
    monkeypatch.setattr(booking_flow.calendar_service, "push_cancel", lambda _db, b: removed.append(b.google_event_id) or True)

    with pytest.raises(booking_service.SlotTakenError):
        _update(db, booking, employee_id=second_employee.id)

    assert removed == []
    db.refresh(booking)
    assert booking.employee_id == employee.id and booking.google_event_id == "evt-old"


def test_отменённую_запись_можно_править_без_проверки_времени(db, client, employee, service, workday):
    booking = _create(db, client, employee, service, workday, hh=10)
    _create(db, client, employee, service, workday, hh=12)
    booking_flow.cancel(db, booking.id, actor="test")

    updated, google = _update(db, booking, start_at=_at(workday, 12), notes="архивная правка")

    assert updated.status == CANCELLED and updated.notes == "архивная правка"
    assert google is None
