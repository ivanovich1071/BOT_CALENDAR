"""Админка: карточка записи — правка всех полей, удаление, права."""

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.bot import services as bot
from app.db.database import get_db
from app.main import app
from app.models.booking import Booking
from app.models.user import User
from app.services.schedule_service import local_tz


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _err(response) -> bool:
    return response.status_code == 303 and "err=" in response.headers["location"]


@pytest.fixture
def booking(db, client, employee, service, workday):
    card = bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    return db.get(Booking, card["id"])


def _form(booking, workday, **changes) -> dict:
    data = {
        "client_id": str(booking.client_id),
        "employee_id": str(booking.employee_id),
        "service_id": str(booking.service_id),
        "date": workday.isoformat(),
        "time": "10:00",
        "notes": "",
    }
    data.update(changes)
    return data


def test_карточка_открывается(admin_client, booking):
    page = admin_client.get(f"/admin/bookings/{booking.id}")
    assert page.status_code == 200
    assert f"Запись #{booking.id}" in page.text and "Пётр" in page.text and "booking.create" in page.text


def test_правка_времени_и_заметки(admin_client, db, booking, workday):
    response = admin_client.post(
        f"/admin/bookings/{booking.id}/update",
        data=_form(booking, workday, time="15:30", notes="Клиент попросил позже"),
        follow_redirects=False,
    )
    assert _ok(response)
    db.refresh(booking)
    assert booking.start_at.astimezone(local_tz()).strftime("%H:%M") == "15:30"
    assert booking.notes == "Клиент попросил позже"


def test_занятое_время_не_сохраняется(admin_client, db, booking, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="12:00")
    response = admin_client.post(
        f"/admin/bookings/{booking.id}/update", data=_form(booking, workday, time="12:00"), follow_redirects=False
    )
    assert _err(response)
    db.refresh(booking)
    assert booking.start_at.astimezone(local_tz()).strftime("%H:%M") == "10:00"


def test_удаление_записи(admin_client, db, booking):
    booking_id = booking.id
    assert _ok(admin_client.post(f"/admin/bookings/{booking_id}/delete", follow_redirects=False))
    db.expire_all()
    assert db.get(Booking, booking_id) is None


def test_сотрудник_без_права_удаления_получает_отказ(db, booking):
    staff = User(login="staff", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(staff)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: staff
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(f"/admin/bookings/{booking.id}/delete", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert db.get(Booking, booking.id) is not None
