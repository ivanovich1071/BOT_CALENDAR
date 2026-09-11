"""Админка: полная правка услуг и сотрудников, исполнители, архив и удаление."""

from datetime import time

import pytest
from sqlalchemy import select

from app.bot import services as bot
from app.models.employee import Employee
from app.models.schedule import Schedule
from app.models.service import Service
from app.models.user import User


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _err(response) -> bool:
    return response.status_code == 303 and "err=" in response.headers["location"]


def _service(db, name: str) -> Service | None:
    return db.scalar(select(Service).where(Service.name == name))


@pytest.fixture
def petrova(db):
    row = Employee(name="Петрова")
    db.add(row)
    db.commit()
    for weekday in range(5):
        db.add(Schedule(employee_id=row.id, weekday=weekday, start_time=time(9), end_time=time(18)))
    db.commit()
    return row


def _service_form(**changes) -> dict:
    data = {"name": "Диагностика", "duration_minutes": "60", "price_mode": "fixed", "price": "1500", "sort_order": "2", "is_active": "on"}
    data.update(changes)
    return data


# ==== Услуги ====

@pytest.mark.parametrize(("mode", "price", "expected"), [("fixed", "1 500", 1500), ("free", "", 0), ("negotiable", "999", None)])
def test_режимы_цены(admin_client, db, employee, mode, price, expected):
    response = admin_client.post(
        "/admin/services/save", data=_service_form(price_mode=mode, price=price, employees=[str(employee.id)]), follow_redirects=False
    )
    assert _ok(response)
    saved = _service(db, "Диагностика")
    assert (saved.price if saved.price is None else int(saved.price)) == expected


def test_страницы_услуг_открываются(admin_client, db, service):
    assert "Консультация" in admin_client.get("/admin/services").text
    assert admin_client.get("/admin/services/new").status_code == 200
    assert "Кто проводит" in admin_client.get(f"/admin/services/{service.id}/edit").text


def test_снятый_исполнитель_получает_явные_услуги(admin_client, db, service, employee, petrova):
    """У обоих нет привязок (всё умеют); с Петровой снимаем «Диагностику»."""
    admin_client.post("/admin/services/save", data=_service_form(employees=[str(employee.id), str(petrova.id)]), follow_redirects=False)
    diag = _service(db, "Диагностика")
    assert {e["name"] for e in bot.employees_with_schedule(db, diag.id)} == {"Иванов", "Петрова"}

    admin_client.post(
        "/admin/services/save",
        data=_service_form(service_id=str(diag.id), employees=[str(employee.id)]),
        follow_redirects=False,
    )

    db.expire_all()
    assert [e["name"] for e in bot.employees_with_schedule(db, diag.id)] == ["Иванов"]
    assert [s.name for s in db.get(Employee, petrova.id).services] == ["Консультация"]
    assert db.get(Employee, employee.id).services == []


def test_архив_убирает_услугу_из_бота(admin_client, db, service):
    assert _ok(admin_client.post(f"/admin/services/{service.id}/archive", follow_redirects=False))
    assert bot.active_services(db) == []
    assert "Консультация" in admin_client.get("/admin/services?archived=1").text

    admin_client.post(f"/admin/services/{service.id}/restore", follow_redirects=False)
    assert [s["name"] for s in bot.active_services(db)] == ["Консультация"]


def test_услугу_с_записями_не_удалить(admin_client, db, service, employee, client, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    assert _err(admin_client.post(f"/admin/services/{service.id}/delete", follow_redirects=False))
    assert _service(db, "Консультация") is not None


def test_услуга_без_записей_удаляется(admin_client, db, service):
    assert _ok(admin_client.post(f"/admin/services/{service.id}/delete", follow_redirects=False))
    assert _service(db, "Консультация") is None


# ==== Сотрудники ====

def test_карточка_сотрудника_bio_услуги_и_логин(admin_client, db, employee, service):
    response = admin_client.post(
        f"/admin/employees/{employee.id}/update",
        data={
            "name": "Иванов",
            "bio": "Диагност, 10 лет практики",
            "services": [str(service.id)],
            "is_active": "on",
            "with_login": "on",
            "login": "ivanov",
            "password": "длинный-пароль",
            "role": "manager",
        },
        follow_redirects=False,
    )
    assert _ok(response)
    db.expire_all()
    saved = db.get(Employee, employee.id)
    assert saved.bio == "Диагност, 10 лет практики"
    assert [s.name for s in saved.services] == ["Консультация"]
    account = db.get(User, saved.user_id)
    assert (account.login, account.role) == ("ivanov", "manager")


def test_страницы_сотрудников_открываются(admin_client, employee):
    assert "Иванов" in admin_client.get("/admin/employees").text
    assert admin_client.get("/admin/employees/new").status_code == 200
    assert "О специалисте" in admin_client.get(f"/admin/employees/{employee.id}/edit").text


def test_архив_скрывает_сотрудника_и_отключает_вход(admin_client, db, employee):
    account = User(login="ivanov", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(account)
    db.commit()
    employee.user_id = account.id
    db.commit()

    assert _ok(admin_client.post(f"/admin/employees/{employee.id}/archive", follow_redirects=False))

    db.expire_all()
    assert bot.employees_with_schedule(db) == []
    assert db.get(User, account.id).is_active is False


def test_сотрудника_с_записями_не_удалить(admin_client, db, employee, service, client, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    assert _err(admin_client.post(f"/admin/employees/{employee.id}/delete", follow_redirects=False))


def test_сотрудник_без_записей_удаляется_с_расписанием(admin_client, db, employee):
    employee_id = employee.id
    assert _ok(admin_client.post(f"/admin/employees/{employee_id}/delete", follow_redirects=False))
    db.expire_all()
    assert db.get(Employee, employee_id) is None
    assert db.scalars(select(Schedule).where(Schedule.employee_id == employee_id)).all() == []
