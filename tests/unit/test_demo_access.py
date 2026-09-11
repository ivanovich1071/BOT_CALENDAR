"""Демо-доступ: гость меняет только демо-данные, бот не видит Демо-специалиста, ночной сброс."""

from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.dependencies import get_current_user
from app.bot import services as bot
from app.db.database import get_db
from app.main import app
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import BOOKED
from app.models.outbox import OutboxMessage
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.user import User
from app.services import booking_service, company_pack, demo_service
from app.services.app_settings_service import COMPANY, DEMO, get_setting


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _denied(response) -> bool:
    return response.status_code == 303 and "демо-доступе" in unquote_plus(response.headers["location"])


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def demo(db):
    demo_service.setup(db, actor="test")
    return demo_service.demo_employee(db)


def _as(db, login: str) -> TestClient:
    user = db.scalar(select(User).where(User.login == login))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _create_form(employee_id, service, workday, slot, **extra) -> dict:
    data = {
        "employee_id": str(employee_id),
        "service_id": str(service.id),
        "client_id": "0",
        "client_name": "Гость",
        "date": workday.isoformat(),
        "slot": slot,
    }
    data.update(extra)
    return data


def test_демо_доступ_заводится_один_раз_и_скрыт_от_бота(db, employee, service, client, workday, demo):
    logins = get_setting(db, DEMO)["logins"]
    demo_service.setup(db, actor="test")
    assert get_setting(db, DEMO)["logins"] == logins and len(logins) == 2
    assert db.scalar(select(func.count()).select_from(Employee).where(Employee.is_demo.is_(True))) == 1
    assert len(db.scalars(select(Schedule).where(Schedule.employee_id == demo.id)).all()) == 5
    assert demo.user_id == db.scalar(select(User.id).where(User.login == demo_service.EMPLOYEE_LOGIN))

    assert [e["name"] for e in bot.employees_with_schedule(db)] == ["Иванов"]
    with pytest.raises(booking_service.NotFoundError):
        bot.create(db, client_id=client.id, employee_id=demo.id, service_id=service.id, day=workday, slot="10:00")

    pack = company_pack.export_pack(db)
    assert [e["name"] for e in pack["employees"]] == ["Иванов"]
    assert demo_service.EMPLOYEE_NAME not in company_pack.plan_import(db, pack).employees_archived


def test_гость_не_меняет_настоящие_данные(db, employee, demo):
    guest = _as(db, demo_service.ADMIN_LOGIN)
    attempts = [
        ("/admin/company/profile", {"name": "Взлом"}),
        ("/admin/users/save", {"login": "hacker", "password": "12345678", "role": "admin"}),
        ("/admin/services/save", {"name": "Новая", "duration_minutes": "30"}),
        ("/admin/knowledge/save", {"title": "x", "body": "y"}),
        ("/admin/schedule/save", {"employee_id": str(employee.id)}),
        ("/admin/schedule/exceptions/add", {"employee_id": str(employee.id), "kind": "day_off", "date_from": "2026-10-01"}),
        (f"/admin/employees/{employee.id}/update", {"name": "Взлом"}),
        ("/admin/settings/google", {"enabled": "0"}),
        ("/admin/settings/demo", {"show_on_login": "on"}),
        ("/admin/settings/demo/reset", {}),
    ]
    for path, data in attempts:
        assert _denied(guest.post(path, data=data, follow_redirects=False)), path
    assert _denied(guest.get(f"/admin/calendars/connect/{employee.id}", follow_redirects=False))
    assert get_setting(db, COMPANY)["name"] != "Взлом"
    assert db.scalar(select(User).where(User.login == "hacker")) is None
    assert not get_setting(db, DEMO)["show_on_login"]

    for page in ("/admin/company", "/admin/users", "/admin/settings", "/admin/calendar"):
        response = guest.get(page)
        assert response.status_code == 200 and "Демо-доступ." in response.text, page


def test_гость_записывает_к_настоящему_сотруднику_без_уведомления(db, employee, service, workday, demo):
    employee.telegram_user_id = 555
    db.commit()
    guest = _as(db, demo_service.ADMIN_LOGIN)
    assert _ok(guest.post("/admin/bookings/create", data=_create_form(employee.id, service, workday, "10:00"), follow_redirects=False))
    booking = db.scalar(select(Booking))
    assert booking.is_demo and booking.client.is_demo
    assert db.scalar(select(OutboxMessage)) is None

    update = {
        "client_id": str(booking.client_id), "employee_id": str(employee.id), "service_id": str(service.id),
        "date": workday.isoformat(), "time": "11:00", "notes": "проба",
    }
    assert _ok(guest.post(f"/admin/bookings/{booking.id}/update", data=update, follow_redirects=False))


def test_запись_из_бота_гостю_не_изменить(db, employee, service, client, workday, demo):
    card = bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    guest = _as(db, demo_service.ADMIN_LOGIN)
    response = guest.post(f"/admin/bookings/{card['id']}/status", data={"status": "cancelled"}, follow_redirects=False)
    assert _denied(response)
    assert db.get(Booking, card["id"]).status == BOOKED


def test_демо_сотрудник_ставит_выходной_демо_специалисту(db, demo, workday):
    guest = _as(db, demo_service.EMPLOYEE_LOGIN)
    data = {"employee_id": str(demo.id), "kind": "day_off", "date_from": workday.isoformat(),
            "date_to": workday.isoformat(), "note": "Проба"}
    assert _ok(guest.post("/admin/schedule/exceptions/add", data=data, follow_redirects=False))
    assert db.scalar(select(ScheduleException).where(ScheduleException.employee_id == demo.id)).note == "Проба"


def test_ночной_сброс_убирает_только_пробы_гостей(db, employee, service, client, workday, demo):
    real = bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    guest = _as(db, demo_service.ADMIN_LOGIN)
    assert _ok(guest.post("/admin/bookings/create", data=_create_form(employee.id, service, workday, "12:00"), follow_redirects=False))
    assert _ok(guest.post(
        "/admin/bookings/create",
        data=_create_form(demo.id, service, workday, "11:00", client_id=str(client.id)),
        follow_redirects=False,
    ))
    db.add(ScheduleException(employee_id=demo.id, date_from=workday, date_to=workday, kind="day_off"))
    guest_user = db.scalar(select(User).where(User.login == demo_service.ADMIN_LOGIN))
    guest_user.permissions = []
    db.commit()

    assert demo_service.reset(db, actor="test") == {"bookings": 2, "clients": 1}
    assert [b.id for b in db.scalars(select(Booking)).all()] == [real["id"]]
    assert db.scalar(select(Client).where(Client.name == "Гость")) is None
    assert db.get(Client, client.id) is not None
    assert db.scalars(select(ScheduleException)).all() == []
    assert len(db.scalars(select(Schedule).where(Schedule.employee_id == demo.id)).all()) == 5
    assert "manage_settings" in db.scalar(select(User).where(User.login == demo_service.ADMIN_LOGIN)).permissions


def test_демо_логины_на_странице_входа_только_по_галочке(db, admin_client, demo):
    assert demo_service.ADMIN_LOGIN not in admin_client.get("/login").text
    assert _ok(admin_client.post("/admin/settings/demo", data={"show_on_login": "on"}, follow_redirects=False))
    page = admin_client.get("/login").text
    password = get_setting(db, DEMO)["logins"][0]["password"]
    assert "Демо-доступ" in page and demo_service.ADMIN_LOGIN in page and password in page
