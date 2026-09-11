"""Общие фикстуры тестов.

Тесты работают на отдельной базе booking_test в том же PostgreSQL, что и dev:
схема создаётся из моделей, между тестами таблицы очищаются. Если PostgreSQL
не поднят — тесты, которым нужна БД, пропускаются, а чистые unit-тесты идут.
"""

from datetime import time, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — регистрация моделей в metadata
from app.config.settings import get_settings
from app.db.database import Base
from app.models.client import Client
from app.models.employee import Employee
from app.models.schedule import Schedule
from app.models.service import Service
from app.services.schedule_service import local_now

TEST_DB_NAME = "booking_test"

TABLES = (
    "outbox, ai_messages, knowledge_articles, schedule_exceptions, employee_services, "
    "notifications, bookings, calendars, google_accounts, schedules, "
    "employees, services, clients, audit_logs, users, app_settings"
)


def _database_urls() -> tuple[str, str]:
    """(URL служебной базы postgres, URL тестовой базы)."""
    base, _, _name = get_settings().database_url.rpartition("/")
    return f"{base}/postgres", f"{base}/{TEST_DB_NAME}"


@pytest.fixture(scope="session")
def engine():
    admin_url, test_url = _database_urls()
    try:
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
        admin_engine.dispose()
    except OperationalError:
        pytest.skip("PostgreSQL недоступен — тесты с БД пропущены")

    test_engine = create_engine(test_url)
    # Схему пересоздаём целиком: create_all не добавляет новые колонки в существующие
    # таблицы, а drop_all спотыкается о цикл внешних ключей employees ↔ calendars
    with test_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db(engine):
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def admin_client(db):
    """Админка от имени администратора: пользователь в тестовой базе, сессия — тестовая."""
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_current_user
    from app.db.database import get_db
    from app.main import app
    from app.models.user import User

    admin = User(login="admin", password_hash="x", role="admin", permissions=[], is_active=True)
    db.add(admin)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: db
    # Без with: lifespan не запускается, планировщик не стартует
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def employee(db):
    """Сотрудник с расписанием пн–пт 09:00–18:00, перерыв 13:00–14:00."""
    row = Employee(name="Иванов")
    db.add(row)
    db.commit()
    for weekday in range(5):
        db.add(
            Schedule(
                employee_id=row.id,
                weekday=weekday,
                start_time=time(9, 0),
                end_time=time(18, 0),
                break_start=time(13, 0),
                break_end=time(14, 0),
            )
        )
    db.commit()
    return row


@pytest.fixture
def service(db):
    row = Service(name="Консультация", duration_minutes=60)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def client(db):
    row = Client(name="Пётр", phone="+7 900 000-00-00")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def workday():
    """Ближайший понедельник в будущем — расписание в фикстуре employee его покрывает."""
    day = local_now().date() + timedelta(days=1)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day
