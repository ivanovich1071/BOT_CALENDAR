"""Единая точка бронирования: связка БД + Google + аудит."""

from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import CANCELLED, SOURCE_ADMIN
from app.services import booking_flow, booking_service
from app.services.schedule_service import local_tz


def _at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=local_tz())


def _create(db, client, employee, service, workday, hh=10):
    return booking_flow.create(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=_at(workday, hh),
        source=SOURCE_ADMIN,
        actor="admin",
    )


def _actions(db) -> list[str]:
    return [a for a in db.scalars(select(AuditLog.action)).all()]


def test_без_google_запись_создаётся_молча(db, client, employee, service, workday):
    booking, google = _create(db, client, employee, service, workday)
    assert booking.id is not None
    assert google is None  # календарь не подключён — предупреждать не о чем
    assert "booking.create" in _actions(db)


def test_сбой_google_не_отменяет_бронь(db, client, employee, service, workday, monkeypatch):
    def взрыв(_db, _booking):
        raise RuntimeError("Google недоступен")

    monkeypatch.setattr(booking_flow.calendar_service, "push_booking", взрыв)
    booking, google = _create(db, client, employee, service, workday)

    assert booking.id is not None
    assert google is False
    assert "google.push_failed" in _actions(db)


def test_недоступность_google_не_ломает_выдачу_слотов(db, employee, service, workday, monkeypatch):
    def взрыв(*_a, **_kw):
        raise RuntimeError("Google недоступен")

    monkeypatch.setattr(booking_flow.calendar_service, "get_busy_intervals", взрыв)
    slots = booking_flow.available_slots(db, employee.id, service.id, workday)
    assert slots and slots[0].strftime("%H:%M") == "09:00"


def test_занятость_google_убирает_слот(db, employee, service, workday, monkeypatch):
    busy_start = _at(workday, 11)
    monkeypatch.setattr(
        booking_flow.calendar_service,
        "get_busy_intervals",
        lambda *a, **kw: [(busy_start, busy_start + timedelta(hours=1))],
    )
    times = [s.strftime("%H:%M") for s in booking_flow.available_slots(db, employee.id, service.id, workday)]
    assert "11:00" not in times
    assert "12:00" in times


def test_при_переносе_запись_не_конфликтует_со_своим_событием(
    db, client, employee, service, workday, monkeypatch
):
    """Событие самой брони лежит в Google и не должно закрывать соседние слоты."""
    booking, _ = _create(db, client, employee, service, workday, hh=10)
    monkeypatch.setattr(
        booking_flow.calendar_service,
        "get_busy_intervals",
        lambda *a, **kw: [(booking.start_at, booking.end_at)],
    )
    moved, _google = booking_flow.reschedule(
        db, booking.id, new_start=_at(workday, 10, 30), actor="admin"
    )
    assert moved.start_at.astimezone(local_tz()).strftime("%H:%M") == "10:30"


def test_отмена_идёт_через_полный_путь(db, client, employee, service, workday, monkeypatch):
    booking, _ = _create(db, client, employee, service, workday)
    удалено = []
    monkeypatch.setattr(
        booking_flow.calendar_service,
        "push_cancel",
        lambda _db, b: удалено.append(b.id) or True,
    )
    cancelled, google = booking_flow.set_status(db, booking.id, CANCELLED, actor="admin")

    assert cancelled.status == CANCELLED
    assert google is True and удалено == [booking.id]
    assert "booking.cancelled" in _actions(db)


def test_перенос_несуществующей_записи(db):
    with pytest.raises(booking_service.NotFoundError):
        booking_flow.reschedule(db, 999, new_start=datetime.now(local_tz()), actor="admin")
