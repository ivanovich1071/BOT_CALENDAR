"""Форма напоминаний в /admin/settings: отрисовка, сохранение, отказ на мусоре."""

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.main import app
from app.models.user import User
from app.services.app_settings_service import REMINDERS, get_setting


@pytest.fixture
def admin_client(db):
    user = User(id=1, login="admin", role="admin", permissions=[], is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    # Без with: lifespan не запускается, планировщик не стартует
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_страница_показывает_текущие_значения(admin_client):
    response = admin_client.get("/admin/settings")
    assert response.status_code == 200
    assert "Напоминания клиентам" in response.text
    assert 'value="24, 1"' in response.text


def test_сохранение(admin_client, db):
    response = admin_client.post(
        "/admin/settings/reminders", data={"hours": "1, 3"}, follow_redirects=False
    )
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    assert get_setting(db, REMINDERS) == {"enabled": False, "hours_before": [3, 1]}


def test_мусор_не_сохраняется(admin_client, db):
    response = admin_client.post(
        "/admin/settings/reminders", data={"enabled": "on", "hours": "завтра"}, follow_redirects=False
    )
    assert response.status_code == 303 and "err=" in response.headers["location"]
    assert get_setting(db, REMINDERS)["hours_before"] == [24, 1]
