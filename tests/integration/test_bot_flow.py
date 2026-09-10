"""Сквозной прогон диалога бота через диспетчер.

Telegram не участвует: сессия подменена записывающей заглушкой, апдейты
собираются вручную. Проверяем то, что увидит клиент, — тексты и кнопки.
"""

from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot import texts
from app.bot.keyboards.callbacks import CalendarCB, ConfirmCB, EmployeeCB, ServiceCB, SlotCB
from app.bot.main import build_dispatcher

TG_USER_ID = 777000111
CHAT_ID = 777000111


class RecordingSession(BaseSession):
    """Вместо запросов в Telegram складывает вызовы в список."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self._message_id = 100

    async def close(self) -> None:  # pragma: no cover — сеть не задействована
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            self._message_id += 1
            return Message(
                message_id=self._message_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=CHAT_ID, type="private"),
                text=method.text,
            ).as_(bot)
        return True

    def sent(self) -> list[str]:
        return [
            m.text
            for m in self.calls
            if isinstance(m, (SendMessage, EditMessageText)) and m.text
        ]

    def last_markup(self):
        for method in reversed(self.calls):
            if isinstance(method, (SendMessage, EditMessageText, EditMessageReplyMarkup)):
                if method.reply_markup is not None:
                    return method.reply_markup
        return None

    def buttons(self) -> list[str]:
        markup = self.last_markup()
        if markup is None or not getattr(markup, "inline_keyboard", None):
            return []
        return [b.text for row in markup.inline_keyboard for b in row]

    def button_data(self, text: str) -> str:
        markup = self.last_markup()
        for row in markup.inline_keyboard:
            for button in row:
                if button.text == text:
                    return button.callback_data
        raise AssertionError(f"кнопка «{text}» не найдена среди {self.buttons()}")

    def clear(self) -> None:
        self.calls.clear()


@pytest.fixture(autouse=True)
def _handlers_use_test_db(engine, monkeypatch):
    """run_db открывает сессию сам — направляем её в тестовую базу, а не в рабочую."""
    from sqlalchemy.orm import sessionmaker

    import app.bot.db as bot_db

    monkeypatch.setattr(
        bot_db,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )


@pytest.fixture(scope="module")
def dispatcher():
    """Диспетчер один на модуль: роутеры — синглтоны и ко второму не подключаются."""
    return build_dispatcher(MemoryStorage())


@pytest.fixture
def tg(dispatcher):
    session = RecordingSession()
    bot = Bot("42:TESTTOKEN", session=session, default=DefaultBotProperties())
    yield bot, dispatcher, session
    dispatcher.storage.storage.clear()  # состояние диалога не течёт в следующий тест


def _user() -> User:
    return User(id=TG_USER_ID, is_bot=False, first_name="Пётр", username="petr")


def _message(text: str) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=CHAT_ID, type="private"),
            from_user=_user(),
            text=text,
        ),
    )


def _callback(data: str) -> Update:
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="cb-1",
            from_user=_user(),
            chat_instance="instance",
            data=data,
            message=Message(
                message_id=100,
                date=datetime.now(timezone.utc),
                chat=Chat(id=CHAT_ID, type="private"),
                text="предыдущее сообщение",
            ),
        ),
    )


async def _feed(tg, update: Update) -> None:
    bot, dispatcher, _ = tg
    await dispatcher.feed_update(bot, update)


@pytest.mark.asyncio
async def test_start_приветствует_и_показывает_меню(tg, db):
    _bot, _dp, session = tg
    await _feed(tg, _message("/start"))

    assert any("Пётр" in text for text in session.sent())
    keyboard = session.calls[0].reply_markup.keyboard
    assert [b.text for row in keyboard for b in row] == [
        texts.BTN_BOOK,
        texts.BTN_MY,
        texts.BTN_INFO,
    ]


@pytest.mark.asyncio
async def test_запись_без_услуг_не_обрывается_молча(tg, db):
    _bot, _dp, session = tg
    await _feed(tg, _message(texts.BTN_BOOK))
    assert texts.NO_SERVICES in session.sent()


@pytest.mark.asyncio
async def test_полный_путь_записи(tg, db, employee, service, workday):
    """Услуга → специалист → дата → время → подтверждение → запись в базе."""
    _bot, _dp, session = tg

    await _feed(tg, _message("/start"))
    session.clear()

    # Услуги
    await _feed(tg, _message(texts.BTN_BOOK))
    assert texts.CHOOSE_SERVICE in session.sent()
    assert any("Консультация" in b for b in session.buttons())
    session.clear()

    # Специалисты
    await _feed(tg, _callback(ServiceCB(service_id=service.id).pack()))
    assert texts.CHOOSE_EMPLOYEE in session.sent()
    assert any("Иванов" in b for b in session.buttons())
    session.clear()

    # Календарь
    await _feed(tg, _callback(EmployeeCB(employee_id=employee.id).pack()))
    assert texts.CHOOSE_DAY in session.sent()
    assert any(isinstance(m, DeleteMessage) for m in session.calls)
    session.clear()

    # Свободное время на рабочий день
    await _feed(
        tg,
        _callback(
            CalendarCB(
                action="day", year=workday.year, month=workday.month, day=workday.day
            ).pack()
        ),
    )
    assert any(texts.human_date(workday) in text for text in session.sent())
    assert "09:00" in session.buttons()
    session.clear()

    # Подтверждение
    await _feed(tg, _callback(SlotCB(hour=9, minute=0).pack()))
    confirmation = " ".join(session.sent())
    assert "Иванов" in confirmation and "09:00" in confirmation and "10:00" in confirmation
    assert texts.BTN_YES in session.buttons()
    session.clear()

    # Запись создана
    await _feed(tg, _callback(ConfirmCB(action="yes").pack()))
    result = " ".join(session.sent())
    assert "Записал вас" in result and "09:00" in result

    from app.bot import services as bot_services

    client = bot_services.get_or_create_client(db, TG_USER_ID, "petr", "Пётр")
    bookings = bot_services.my_bookings(db, client["id"])
    assert len(bookings) == 1 and bookings[0]["start"] == "09:00"


@pytest.mark.asyncio
async def test_нерабочий_день_не_ведёт_дальше(tg, db, employee, service, workday):
    """Суббота в календаре нарисована точкой; её callback — ignore."""
    _bot, _dp, session = tg
    await _feed(tg, _message(texts.BTN_BOOK))
    await _feed(tg, _callback(ServiceCB(service_id=service.id).pack()))
    await _feed(tg, _callback(EmployeeCB(employee_id=employee.id).pack()))
    session.clear()

    await _feed(tg, _callback(CalendarCB(action="ignore", year=0, month=0, day=0).pack()))
    assert session.sent() == []
    assert any(isinstance(m, AnswerCallbackQuery) for m in session.calls)


@pytest.mark.asyncio
async def test_мои_записи_и_отмена(tg, db, employee, service, workday):
    _bot, _dp, session = tg
    from app.bot import services as bot_services

    client = bot_services.get_or_create_client(db, TG_USER_ID, "petr", "Пётр")
    card = bot_services.create(
        db,
        client_id=client["id"],
        employee_id=employee.id,
        service_id=service.id,
        day=workday,
        slot="10:00",
    )

    await _feed(tg, _message(texts.BTN_MY))
    assert texts.MY_BOOKINGS in session.sent()
    assert any("Иванов" in text for text in session.sent())
    assert texts.BTN_CANCEL_BOOKING in session.buttons()
    cancel_data = session.button_data(texts.BTN_CANCEL_BOOKING)
    session.clear()

    await _feed(tg, _callback(cancel_data))
    assert any("Отменить эту запись" in text for text in session.sent())
    confirm_data = session.button_data("❌ Да, отменить")
    session.clear()

    await _feed(tg, _callback(confirm_data))
    assert texts.CANCELLED in session.sent()
    assert bot_services.my_bookings(db, client["id"]) == []
    assert card["id"]


@pytest.mark.asyncio
async def test_чужая_запись_недоступна(tg, db, employee, service, workday, client):
    """Клиент бота не должен трогать бронь другого человека."""
    _bot, _dp, session = tg
    from app.bot.keyboards.callbacks import BookingCB
    from app.models.enums import SOURCE_ADMIN
    from app.services import booking_flow
    from app.bot import services as bot_services

    other, _ = booking_flow.create(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=bot_services.parse_slot(workday, "11:00"),
        source=SOURCE_ADMIN,
        actor="admin",
    )
    await _feed(tg, _callback(BookingCB(action="cancel", booking_id=other.id).pack()))

    answers = [m for m in session.calls if isinstance(m, AnswerCallbackQuery)]
    assert answers and answers[-1].text == texts.NOT_FOUND
    assert session.sent() == []


# ==== Свободный текст (AI) ====


class _FakeAI:
    """Вместо OpenRouter: отдаёт заданное намерение и запоминает, что спрашивали."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fields: dict = {"action": "unknown"}

    def answer(self, **fields) -> None:
        self.fields = fields


@pytest.fixture
def fake_ai(monkeypatch):
    import app.bot.handlers.ai as ai_handler
    from app.ai import intent as ai_intent
    from app.bot.ratelimit import SlidingWindowLimiter
    from app.config.settings import Settings

    fake = _FakeAI()

    async def fake_parse(text, services, employees, today):
        fake.calls.append(text)
        return ai_intent.Intent(**fake.fields)

    monkeypatch.setattr(ai_intent, "parse", fake_parse)
    monkeypatch.setattr(
        ai_handler, "get_settings", lambda: Settings(_env_file=None, openrouter_api_key="sk-or-test")
    )
    monkeypatch.setattr(ai_handler, "ai_limiter", SlidingWindowLimiter(100, 3600))
    return fake


@pytest.mark.asyncio
async def test_фраза_ведёт_к_слотам_и_записи(tg, db, employee, service, workday, fake_ai):
    """«На консультацию в понедельник утром» → утренние слоты → нажатие → подтверждение → запись."""
    import datetime as dt

    from app.bot import services as bot_services

    _bot, _dp, session = tg
    fake_ai.answer(
        action="book", service="консультацию", date=workday,
        time_from=dt.time(9), time_to=dt.time(12),
    )

    await _feed(tg, _message("хочу на консультацию в понедельник утром"))
    assert any("Нашёл свободное время" in text for text in session.sent())
    buttons = session.buttons()
    assert "09:00" in buttons and all(b < "12:00" for b in buttons)
    session.clear()

    # Дальше — обычные кнопки записи, без всякого AI
    await _feed(tg, _callback(SlotCB(hour=9, minute=0).pack()))
    assert texts.BTN_YES in session.buttons()
    session.clear()

    await _feed(tg, _callback(ConfirmCB(action="yes").pack()))
    assert any("Записал вас" in text for text in session.sent())

    client = bot_services.get_or_create_client(db, TG_USER_ID, "petr", "Пётр")
    [booking] = bot_services.my_bookings(db, client["id"])
    assert booking["start"] == "09:00"


@pytest.mark.asyncio
async def test_без_даты_фраза_открывает_календарь(tg, db, employee, service, fake_ai):
    _bot, _dp, session = tg
    fake_ai.answer(action="book", service="Консультация")
    await _feed(tg, _message("запишите меня на консультацию"))
    assert texts.CHOOSE_DAY in session.sent()


@pytest.mark.asyncio
async def test_занятое_окно_показывает_весь_день(tg, db, employee, service, workday, fake_ai):
    import datetime as dt

    _bot, _dp, session = tg
    fake_ai.answer(action="book", date=workday, time_from=dt.time(19), time_to=dt.time(21))
    await _feed(tg, _message("в понедельник вечером"))
    assert any("всё занято" in text for text in session.sent())
    assert "09:00" in session.buttons()


@pytest.mark.asyncio
async def test_мои_записи_текстом(tg, db, fake_ai):
    _bot, _dp, session = tg
    fake_ai.answer(action="my_bookings")
    await _feed(tg, _message("когда я записан?"))
    assert texts.NO_BOOKINGS in session.sent()


@pytest.mark.asyncio
async def test_непонятная_фраза_не_создаёт_записи(tg, db, employee, service, fake_ai):
    from sqlalchemy import func, select

    from app.models.booking import Booking

    _bot, _dp, session = tg
    fake_ai.answer(action="unknown")
    await _feed(tg, _message("какая погода на Марсе"))
    assert texts.AI_NOT_UNDERSTOOD in session.sent()
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


@pytest.mark.asyncio
async def test_кнопки_и_команды_не_уходят_в_ai(tg, db, fake_ai):
    _bot, _dp, session = tg
    await _feed(tg, _message(texts.BTN_BOOK))
    await _feed(tg, _message("/неизвестная"))
    assert fake_ai.calls == []
    assert texts.NO_SERVICES in session.sent()


@pytest.mark.asyncio
async def test_без_ключа_бот_просит_кнопки(tg, db, monkeypatch):
    import app.bot.handlers.ai as ai_handler
    from app.config.settings import Settings

    monkeypatch.setattr(
        ai_handler, "get_settings", lambda: Settings(_env_file=None, openrouter_api_key="")
    )
    _bot, _dp, session = tg
    await _feed(tg, _message("хочу записаться"))
    assert texts.AI_DISABLED in session.sent()
