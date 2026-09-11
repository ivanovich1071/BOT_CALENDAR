"""Админка: пользователи и роли, карточка клиента, архив и удаление данных клиента."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import get_current_user
from app.bot import services as bot
from app.db.database import get_db
from app.main import app
from app.models.ai_message import AiMessage
from app.models.booking import Booking
from app.models.client import Client
from app.models.user import User


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _err(response) -> bool:
    return response.status_code == 303 and "err=" in response.headers["location"]


def _user(db, login: str) -> User | None:
    return db.scalar(select(User).where(User.login == login))


@pytest.fixture
def manager_client(db):
    manager = User(login="manager", password_hash="x", role="manager", permissions=[], is_active=True)
    db.add(manager)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: manager
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ==== Пользователи ====

def test_пользователь_создаётся_и_правится(admin_client, db):
    created = admin_client.post(
        "/admin/users/save",
        data={"login": "natalia", "password": "длинный-пароль", "role": "manager", "is_active": "on"},
        follow_redirects=False,
    )
    assert _ok(created)
    natalia = _user(db, "natalia")
    assert natalia.role == "manager" and natalia.password_hash != "длинный-пароль"

    old_hash = natalia.password_hash
    edited = admin_client.post(
        "/admin/users/save",
        data={"user_id": str(natalia.id), "login": "natalia", "role": "viewer", "permissions": ["view_clients"], "is_active": "on"},
        follow_redirects=False,
    )
    assert _ok(edited)
    db.refresh(natalia)
    assert (natalia.role, natalia.permissions, natalia.password_hash) == ("viewer", ["view_clients"], old_hash)


def test_короткий_пароль_и_занятый_логин(admin_client, db):
    assert _err(admin_client.post("/admin/users/save", data={"login": "short", "password": "123", "role": "viewer"}, follow_redirects=False))
    assert _err(admin_client.post("/admin/users/save", data={"login": "admin", "password": "длинный-пароль", "role": "viewer"}, follow_redirects=False))
    assert _user(db, "short") is None


def test_себя_нельзя_разжаловать_и_удалить(admin_client, db):
    me = _user(db, "admin")
    demote = admin_client.post(
        "/admin/users/save", data={"user_id": str(me.id), "login": "admin", "role": "viewer", "is_active": "on"}, follow_redirects=False
    )
    assert _err(demote)
    assert _err(admin_client.post(f"/admin/users/{me.id}/delete", follow_redirects=False))
    db.refresh(me)
    assert me.role == "admin"


def test_привязка_к_сотруднику_и_удаление(admin_client, db, employee):
    admin_client.post(
        "/admin/users/save",
        data={"login": "ivanov", "password": "длинный-пароль", "role": "employee", "employee_id": str(employee.id), "is_active": "on"},
        follow_redirects=False,
    )
    ivanov = _user(db, "ivanov")
    db.refresh(employee)
    assert employee.user_id == ivanov.id

    assert _ok(admin_client.post(f"/admin/users/{ivanov.id}/delete", follow_redirects=False))
    db.refresh(employee)
    assert _user(db, "ivanov") is None and employee.user_id is None


def test_страницы_пользователей_открываются(admin_client, db, employee):
    me = _user(db, "admin")
    assert "admin" in admin_client.get("/admin/users").text
    new_page = admin_client.get("/admin/users/new")
    assert new_page.status_code == 200 and "Иванов" in new_page.text and "Администратор" in new_page.text
    assert admin_client.get(f"/admin/users/{me.id}/edit").status_code == 200


def test_менеджер_не_управляет_пользователями(manager_client):
    assert manager_client.get("/admin/users").status_code == 403


# ==== Клиенты ====

def test_карточка_клиента_с_записями(admin_client, db, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    page = admin_client.get(f"/admin/clients/{client.id}")
    assert page.status_code == 200
    assert "Пётр" in page.text and "Иванов" in page.text and "Консультация" in page.text


def test_архив_убирает_клиента_из_списка(admin_client, db, client):
    assert _ok(admin_client.post(f"/admin/clients/{client.id}/archive", follow_redirects=False))
    assert "Пётр" not in admin_client.get("/admin/clients").text
    assert "Пётр" in admin_client.get("/admin/clients?archived=1").text

    admin_client.post(f"/admin/clients/{client.id}/restore", follow_redirects=False)
    assert "Пётр" in admin_client.get("/admin/clients").text


def test_удаление_клиента_с_записями_и_диалогом(admin_client, db, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    db.add(AiMessage(client_id=client.id, role="user", content="вопрос"))
    db.commit()
    client_id = client.id

    assert _ok(admin_client.post(f"/admin/clients/{client_id}/delete", follow_redirects=False))

    db.expire_all()
    assert db.get(Client, client_id) is None
    assert db.scalars(select(Booking).where(Booking.client_id == client_id)).all() == []
    assert db.scalars(select(AiMessage).where(AiMessage.client_id == client_id)).all() == []


def test_менеджер_не_удаляет_клиента(manager_client, db, client):
    assert _err(manager_client.post(f"/admin/clients/{client.id}/delete", follow_redirects=False))
    db.expire_all()
    assert db.get(Client, client.id) is not None
