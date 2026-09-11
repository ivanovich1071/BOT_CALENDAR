"""Сотрудник со своим логином видит и меняет только своё; выходные через админку."""

from datetime import time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import get_current_user
from app.bot import services as bot
from app.db.database import get_db
from app.main import app
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import BOOKED
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.user import User


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _err(response) -> bool:
    return response.status_code == 303 and "err=" in response.headers["location"]


@pytest.fixture
def other(db):
    row = Employee(name="Петрова")
    db.add(row)
    db.commit()
    for weekday in range(5):
        db.add(Schedule(employee_id=row.id, weekday=weekday, start_time=time(9), end_time=time(18)))
    db.commit()
    return row


def _as(db, user: User) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture
def staff_client(db, employee):
    """Логин сотрудника «Иванов» с правами роли по умолчанию."""
    staff = User(login="ivanov", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(staff)
    db.commit()
    employee.user_id = staff.id
    db.commit()
    yield _as(db, staff)
    app.dependency_overrides.clear()


@pytest.fixture
def bookings(db, client, employee, other, service, workday):
    maria = Client(name="Мария")
    db.add(maria)
    db.commit()
    mine = bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    foreign = bot.create(db, client_id=maria.id, employee_id=other.id, service_id=service.id, day=workday, slot="11:00")
    return db.get(Booking, mine["id"]), db.get(Booking, foreign["id"]), maria


def test_видит_только_свои_записи(staff_client, bookings, workday):
    page = staff_client.get(f"/admin/bookings?date={workday.isoformat()}")
    assert page.status_code == 200
    assert "Иванов" in page.text and "Петрова" not in page.text


def test_чужая_запись_закрыта(staff_client, db, bookings, workday):
    _mine, foreign, _maria = bookings
    assert staff_client.get(f"/admin/bookings/{foreign.id}").status_code == 403
    assert _err(staff_client.post(f"/admin/bookings/{foreign.id}/status", data={"status": "cancelled"}, follow_redirects=False))
    update = staff_client.post(
        f"/admin/bookings/{foreign.id}/update",
        data={
            "client_id": str(foreign.client_id), "employee_id": str(foreign.employee_id),
            "service_id": str(foreign.service_id), "date": workday.isoformat(), "time": "15:00",
        },
        follow_redirects=False,
    )
    assert _err(update)
    db.refresh(foreign)
    assert foreign.status == BOOKED


def test_свою_запись_можно_менять(staff_client, db, bookings):
    mine, _foreign, _maria = bookings
    assert staff_client.get(f"/admin/bookings/{mine.id}").status_code == 200
    assert _ok(staff_client.post(f"/admin/bookings/{mine.id}/status", data={"status": "completed"}, follow_redirects=False))


def test_клиенты_только_свои(staff_client, bookings):
    _mine, _foreign, maria = bookings
    listing = staff_client.get("/admin/clients")
    assert "Пётр" in listing.text and "Мария" not in listing.text
    assert staff_client.get(f"/admin/clients/{maria.id}").status_code == 403


def test_расписание_только_своё(staff_client, db, other):
    page = staff_client.get("/admin/schedule")
    assert "Иванов" in page.text and "Петрова" not in page.text
    assert _err(staff_client.post("/admin/schedule/save", data={"employee_id": str(other.id)}, follow_redirects=False))


def test_выходной_через_админку(staff_client, db, employee, workday):
    added = staff_client.post(
        "/admin/schedule/exceptions/add",
        data={"employee_id": str(employee.id), "kind": "day_off", "date_from": workday.isoformat(), "note": "Конференция"},
        follow_redirects=False,
    )
    assert _ok(added)
    [row] = db.scalars(select(ScheduleException)).all()
    assert row.note == "Конференция"
    assert workday not in bot.open_days(db, employee.id, workday, workday + timedelta(days=1))

    assert "Конференция" in staff_client.get("/admin/schedule").text
    assert _ok(staff_client.post(f"/admin/schedule/exceptions/{row.id}/delete", follow_redirects=False))
    assert db.scalars(select(ScheduleException)).all() == []


def test_чужой_выходной_нельзя(staff_client, db, other, workday):
    response = staff_client.post(
        "/admin/schedule/exceptions/add",
        data={"employee_id": str(other.id), "kind": "day_off", "date_from": workday.isoformat()},
        follow_redirects=False,
    )
    assert _err(response)
    assert db.scalars(select(ScheduleException)).all() == []


def test_дополнительное_окно_требует_время(staff_client, db, employee, workday):
    saturday = workday + timedelta(days=5)
    no_time = staff_client.post(
        "/admin/schedule/exceptions/add",
        data={"employee_id": str(employee.id), "kind": "extra", "date_from": saturday.isoformat()},
        follow_redirects=False,
    )
    assert _err(no_time)
    with_time = staff_client.post(
        "/admin/schedule/exceptions/add",
        data={"employee_id": str(employee.id), "kind": "extra", "date_from": saturday.isoformat(), "start_time": "10:00", "end_time": "14:00"},
        follow_redirects=False,
    )
    assert _ok(with_time)
    assert saturday in bot.open_days(db, employee.id, saturday, saturday)


def test_менеджер_видит_всех(db, bookings, workday):
    manager = User(login="manager", password_hash="x", role="manager", permissions=[], is_active=True)
    db.add(manager)
    db.commit()
    try:
        page = _as(db, manager).get(f"/admin/bookings?date={workday.isoformat()}")
    finally:
        app.dependency_overrides.clear()
    assert "Иванов" in page.text and "Петрова" in page.text
