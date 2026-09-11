"""Уведомления сотрудникам: привязка Telegram, очередь, отправка, ссылка из админки."""

from datetime import datetime, time, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import select

import admin.routes.employees as employees_routes
from app.bot import services as bot
from app.bot.notify import send_outbox
from app.config.settings import Settings
from app.models.employee import Employee
from app.models.enums import SOURCE_ADMIN
from app.models.outbox import OutboxMessage
from app.models.schedule import Schedule
from app.models.user import User
from app.services import booking_flow, staff_notify
from app.services.app_settings_service import set_setting
from app.services.schedule_service import local_tz

TG_EMPLOYEE = 555000
TG_OTHER = 777000


@pytest.fixture(autouse=True)
def _run_db_uses_test_db(engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import app.bot.db as bot_db

    monkeypatch.setattr(bot_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


def _outbox(db) -> list[OutboxMessage]:
    db.expire_all()
    return db.scalars(select(OutboxMessage).order_by(OutboxMessage.id)).all()


# ==== Привязка ====

def test_ссылка_привязывает_telegram_один_раз(db, employee):
    token = staff_notify.create_link_token(db, employee.id)
    assert staff_notify.consume_link_token(db, token, TG_EMPLOYEE) == "Иванов"
    db.refresh(employee)
    assert employee.telegram_user_id == TG_EMPLOYEE
    assert staff_notify.consume_link_token(db, token, TG_OTHER) is None


def test_просроченная_ссылка_не_работает(db, employee):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    set_setting(db, staff_notify.STAFF_LINKS, {"old": {"employee_id": employee.id, "expires": past}})
    assert staff_notify.consume_link_token(db, "old", TG_EMPLOYEE) is None


def test_telegram_переходит_от_другого_сотрудника(db, employee):
    other = Employee(name="Петрова", telegram_user_id=TG_EMPLOYEE)
    db.add(other)
    db.commit()
    token = staff_notify.create_link_token(db, employee.id)
    staff_notify.consume_link_token(db, token, TG_EMPLOYEE)
    db.expire_all()
    assert db.get(Employee, other.id).telegram_user_id is None
    assert db.get(Employee, employee.id).telegram_user_id == TG_EMPLOYEE


# ==== Очередь ====

@pytest.fixture
def linked(db, employee):
    employee.telegram_user_id = TG_EMPLOYEE
    db.commit()
    return employee


def test_запись_ставит_уведомление(db, client, linked, service, workday):
    bot.create(db, client_id=client.id, employee_id=linked.id, service_id=service.id, day=workday, slot="10:00")
    [message] = _outbox(db)
    assert message.chat_id == TG_EMPLOYEE
    assert "Новая запись" in message.text and "Пётр" in message.text and "10:00 – 11:00" in message.text


def test_без_telegram_уведомления_нет(db, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    assert _outbox(db) == []


def test_перенос_и_отмена(db, client, linked, service, workday):
    card = bot.create(db, client_id=client.id, employee_id=linked.id, service_id=service.id, day=workday, slot="10:00")
    booking_flow.reschedule(db, card["id"], new_start=datetime.combine(workday, time(15), tzinfo=local_tz()), actor="admin")
    booking_flow.cancel(db, card["id"], actor="admin")
    texts = [m.text for m in _outbox(db)]
    assert "перенесена" in texts[1] and "Было:" in texts[1] and "10:00" in texts[1]
    assert "отменена" in texts[2]


def test_автор_правки_сам_себе_не_пишет(db, client, linked, service, workday):
    account = User(login="ivanov", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(account)
    db.commit()
    linked.user_id = account.id
    db.commit()
    booking_flow.create(
        db, client_id=client.id, employee_id=linked.id, service_id=service.id,
        start_at=datetime.combine(workday, time(10), tzinfo=local_tz()), source=SOURCE_ADMIN, actor="ivanov",
    )
    assert _outbox(db) == []


def test_смена_специалиста_уведомляет_обоих(db, client, linked, service, workday):
    other = Employee(name="Петрова", telegram_user_id=TG_OTHER)
    db.add(other)
    db.commit()
    db.add(Schedule(employee_id=other.id, weekday=0, start_time=time(9), end_time=time(18)))
    db.commit()
    card = bot.create(db, client_id=client.id, employee_id=linked.id, service_id=service.id, day=workday, slot="10:00")
    booking_flow.update(
        db, card["id"], client_id=client.id, employee_id=other.id, service_id=service.id,
        start_at=datetime.combine(workday, time(10), tzinfo=local_tz()), notes=None, actor="admin",
    )
    messages = _outbox(db)[1:]
    assert {(m.chat_id, m.text.splitlines()[0]) for m in messages} == {
        (TG_EMPLOYEE, "➡️ Запись передана другому специалисту"),
        (TG_OTHER, "🆕 Новая запись"),
    }


# ==== Отправка ====

class _FakeBot:
    def __init__(self, fail_for: dict[int, Exception] | None = None) -> None:
        self.fail_for = fail_for or {}
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text):
        if chat_id in self.fail_for:
            raise self.fail_for[chat_id]
        self.sent.append((chat_id, text))


async def test_очередь_уходит_один_раз(db):
    db.add_all([OutboxMessage(chat_id=TG_EMPLOYEE, text="раз"), OutboxMessage(chat_id=TG_OTHER, text="два")])
    db.commit()
    fake = _FakeBot()
    assert await send_outbox(fake) == 2
    assert await send_outbox(fake) == 0
    assert all(m.sent_at is not None for m in _outbox(db))


async def test_заблокированный_бот_не_повторяется_а_сбой_сети_повторяется(db):
    db.add_all([OutboxMessage(chat_id=TG_EMPLOYEE, text="раз"), OutboxMessage(chat_id=TG_OTHER, text="два")])
    db.commit()
    method = SendMessage(chat_id=TG_EMPLOYEE, text="раз")
    fake = _FakeBot(
        {
            TG_EMPLOYEE: TelegramForbiddenError(method=method, message="Forbidden: bot was blocked"),
            TG_OTHER: TelegramNetworkError(method=method, message="timeout"),
        }
    )
    assert await send_outbox(fake) == 0
    blocked, flaky = _outbox(db)
    assert blocked.attempts == staff_notify.MAX_ATTEMPTS
    assert flaky.attempts == 1 and flaky.sent_at is None
    assert [item["chat_id"] for item in staff_notify.pending(db)] == [TG_OTHER]


# ==== Админка ====

def test_админка_выдаёт_ссылку_и_отвязывает(admin_client, db, linked, monkeypatch):
    async def fake_username(token):
        return "CALENDARentry_bot"

    monkeypatch.setattr(employees_routes, "bot_username", fake_username)
    monkeypatch.setattr(employees_routes, "get_settings", lambda: Settings(_env_file=None, bot_token="1:TEST"))

    page = admin_client.post(f"/admin/employees/{linked.id}/telegram-link")
    assert page.status_code == 200
    assert "https://t.me/CALENDARentry_bot?start=staff_" in page.text

    response = admin_client.post(f"/admin/employees/{linked.id}/telegram-unlink", follow_redirects=False)
    assert response.status_code == 303
    db.refresh(linked)
    assert linked.telegram_user_id is None
