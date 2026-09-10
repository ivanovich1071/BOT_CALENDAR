"""Рассылка напоминаний: текст, кнопки, идемпотентность, сбои Telegram."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy.orm import sessionmaker

from app.bot import texts
from app.bot.notify import send_due_reminders
from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKED


@pytest.fixture(autouse=True)
def _notify_uses_test_db(engine, monkeypatch):
    """run_db открывает сессию сам — направляем её в тестовую базу."""
    import app.bot.db as bot_db

    monkeypatch.setattr(
        bot_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )


class FakeBot:
    """Складывает отправленное; для заданных чатов изображает сбой."""

    def __init__(self, failures: dict | None = None):
        self.sent: list[tuple[int, str, object]] = []
        self.failures = failures or {}

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.failures:
            raise self.failures[chat_id]
        self.sent.append((chat_id, text, reply_markup))


def _network_error():
    return TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="обрыв")


def _forbidden():
    return TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="blocked")


def _tomorrow_booking(db, employee, service, telegram_user_id, now, minutes_shift=0):
    client = Client(name=f"Клиент {telegram_user_id}", telegram_user_id=telegram_user_id)
    db.add(client)
    db.commit()
    start = now + timedelta(hours=24) - timedelta(minutes=minutes_shift)
    row = Booking(
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=BOOKED,
        source="telegram",
        created_at=now - timedelta(days=2),
    )
    db.add(row)
    db.commit()
    return row


async def test_напоминание_уходит_один_раз(db, employee, service):
    now = datetime.now(timezone.utc)
    booking = _tomorrow_booking(db, employee, service, 111, now)
    bot = FakeBot()

    assert await send_due_reminders(bot, now) == 1
    [(chat_id, text, markup)] = bot.sent
    assert chat_id == 111
    assert "завтра" in text and "Иванов" in text and "Консультация" in text
    buttons = [b.text for row in markup.inline_keyboard for b in row]
    assert buttons == [texts.BTN_RESCHEDULE, texts.BTN_CANCEL_BOOKING]
    assert str(booking.id) in markup.inline_keyboard[0][0].callback_data

    assert await send_due_reminders(bot, now + timedelta(minutes=5)) == 0
    assert len(bot.sent) == 1


async def test_обрыв_связи_не_помечает_отправленным(db, employee, service):
    now = datetime.now(timezone.utc)
    _tomorrow_booking(db, employee, service, 222, now)

    assert await send_due_reminders(FakeBot({222: _network_error()}), now) == 0

    retry = FakeBot()
    assert await send_due_reminders(retry, now + timedelta(minutes=5)) == 1
    assert len(retry.sent) == 1


async def test_заблокировавший_бота_не_мешает_остальным(db, employee, service):
    now = datetime.now(timezone.utc)
    _tomorrow_booking(db, employee, service, 333, now)
    _tomorrow_booking(db, employee, service, 444, now, minutes_shift=1)
    bot = FakeBot({333: _forbidden()})

    assert await send_due_reminders(bot, now) == 1
    assert [chat for chat, _, _ in bot.sent] == [444]


@pytest.mark.parametrize(
    "hours, days_ahead, expected",
    [(1, 0, "через час"), (3, 0, "через 3 ч"), (24, 1, "завтра"), (48, 2, "послезавтра"), (72, 3, "через 3 дн.")],
)
def test_как_назвать_момент_визита(hours, days_ahead, expected):
    assert texts.reminder_when(hours, days_ahead) == expected
