"""Сквозной прогон диалога бота через диспетчер.

Telegram не участвует: сессия подменена записывающей заглушкой, апдейты
собираются вручную. Проверяем то, что увидит клиент, — тексты и кнопки.
"""

import json
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
from app.bot.keyboards.callbacks import AiBookCB, CalendarCB, ConfirmCB, EmployeeCB, ServiceCB, SlotCB
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
async def test_приветствие_и_информация_из_профиля_компании(tg, db, service):
    from app.services.app_settings_service import COMPANY, set_setting

    set_setting(
        db,
        COMPANY,
        {"name": "ВайбМайнд", "greeting": "Здравствуйте, {name}! Я консультант ВайбМайнд.", "phone": "+375 29 7-200-700"},
    )
    _bot, _dp, session = tg

    await _feed(tg, _message("/start"))
    assert "Здравствуйте, Пётр! Я консультант ВайбМайнд." in session.sent()
    session.clear()

    await _feed(tg, _message(texts.BTN_INFO))
    info = " ".join(session.sent())
    assert "<b>ВайбМайнд</b>" in info and "Консультация — 60 мин, по договорённости" in info
    assert "+375 29 7-200-700" in info


@pytest.mark.asyncio
async def test_сотрудник_привязывает_telegram_по_ссылке(tg, db, employee):
    from app.services import staff_notify

    token = staff_notify.create_link_token(db, employee.id)
    _bot, _dp, session = tg

    await _feed(tg, _message(f"/start {staff_notify.PAYLOAD_PREFIX}{token}"))

    assert texts.STAFF_LINKED.format(name="Иванов") in session.sent()
    db.refresh(employee)
    assert employee.telegram_user_id == TG_USER_ID


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


# ==== ИИ-консультант ====


class _ScriptedAI:
    """Вместо OpenRouter: отдаёт заранее заданные ответы по очереди и запоминает запросы."""

    def __init__(self) -> None:
        self.replies: list = []
        self.requests: list[list[dict]] = []

    def say(self, text: str) -> None:
        from app.integrations.openrouter.client import ChatReply

        self.replies.append(ChatReply(content=text))

    def call(self, name: str, **args) -> None:
        from app.integrations.openrouter.client import ChatReply

        self.replies.append(
            ChatReply(
                tool_calls=[
                    {
                        "id": f"call_{len(self.replies)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                    }
                ]
            )
        )


@pytest.fixture
def fake_ai(monkeypatch):
    import app.bot.handlers.ai as ai_handler
    from app.ai import agent
    from app.config.settings import Settings
    from app.integrations.openrouter.client import OpenRouterError

    fake = _ScriptedAI()

    async def fake_chat(messages, *, tools=None, model=None, temperature=None, transport=None):
        fake.requests.append(list(messages))
        if not fake.replies:
            raise OpenRouterError("сценарий закончился")
        return fake.replies.pop(0)

    monkeypatch.setattr(agent, "chat", fake_chat)
    monkeypatch.setattr(
        ai_handler, "get_settings", lambda: Settings(_env_file=None, openrouter_api_key="sk-or-test")
    )
    return fake


def _bookings(db):
    from sqlalchemy import select

    from app.models.booking import Booking

    return db.scalars(select(Booking)).all()


@pytest.mark.asyncio
async def test_вопрос_получает_ответ_по_базе_знаний(tg, db, employee, service, fake_ai):
    from sqlalchemy import select

    from app.models.ai_message import AiMessage
    from app.models.knowledge_article import KnowledgeArticle

    db.add(KnowledgeArticle(title="Обучение", body="Модули по 4 академических часа"))
    db.commit()
    _bot, _dp, session = tg
    fake_ai.say("Мы проводим **обучение** и диагностику.")

    await _feed(tg, _message("какие услуги вы оказываете?"))

    assert "Мы проводим <b>обучение</b> и диагностику." in session.sent()
    system = fake_ai.requests[0][0]["content"]
    assert "### Обучение\nМодули по 4 академических часа" in system and "Консультация" in system
    assert [m.role for m in db.scalars(select(AiMessage).order_by(AiMessage.id))] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_история_уходит_в_следующий_запрос(tg, db, fake_ai):
    fake_ai.say("Расскажу об услугах.")
    fake_ai.say("Встреча длится 30 минут.")

    await _feed(tg, _message("здравствуйте"))
    await _feed(tg, _message("а сколько длится?"))

    second = fake_ai.requests[1]
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "user"]
    assert second[1]["content"] == "здравствуйте" and second[-1]["content"] == "а сколько длится?"


def test_выбор_кнопками_строкой_для_консультанта():
    from app.bot.handlers.ai import button_context

    assert button_context({}) is None
    assert button_context(
        {"service_name": "Встреча", "employee_name": "Вероника", "day": "2026-09-17", "slot": "16:30"}
    ) == "услуга «Встреча», специалист Вероника, день 2026-09-17 — 17 сентября, четверг, время 16:30"


@pytest.mark.asyncio
async def test_текст_после_выбора_кнопками_знает_выбранное(tg, db, employee, service, workday, fake_ai):
    """Живая проверка 11.09: «на 17 ч можно?» после выбора в меню модель поняла как «сегодня»."""
    fake_ai.call(
        "propose_booking", service_id=service.id, employee_id=employee.id,
        date=workday.isoformat(), time="11:00", summary="",
    )
    fake_ai.say("Нажмите «Записаться».")
    await _feed(tg, _message("запишите на консультацию"))
    await _feed(tg, _callback(AiBookCB(action="other").pack()))
    fake_ai.say("На какой день?")

    await _feed(tg, _message("на 17 ч можно?"))

    last = fake_ai.requests[-1][-1]
    assert last["role"] == "user"
    assert last["content"] == "[Выбор кнопками: услуга «Консультация», специалист Иванов]\nна 17 ч можно?"


@pytest.mark.asyncio
async def test_диалог_ведёт_к_карточке_и_записи(tg, db, employee, service, workday, fake_ai):
    """Модель ищет время, предлагает карточку; запись создаёт только нажатие «Записаться»."""
    _bot, _dp, session = tg
    fake_ai.call("find_slots", service_id=service.id, date_from=workday.isoformat(), time_from="09:00", time_to="12:00")
    fake_ai.call(
        "propose_booking",
        service_id=service.id,
        employee_id=employee.id,
        date=workday.isoformat(),
        time="10:00",
        summary="Хочет обсудить обучение отдела продаж",
    )
    fake_ai.say("Нашёл время у Иванова — нажмите «Записаться».")

    await _feed(tg, _message("запишите на консультацию в понедельник утром"))

    tool_result = fake_ai.requests[1][-1]
    assert tool_result["role"] == "tool" and '"time": "09:00"' in tool_result["content"]
    assert any("10:00 – 11:00" in text for text in session.sent())
    assert texts.BTN_AI_BOOK in session.buttons()
    assert _bookings(db) == []
    session.clear()

    await _feed(tg, _callback(AiBookCB(action="yes").pack()))

    assert any("Записал вас" in text for text in session.sent())
    [booking] = _bookings(db)
    assert booking.source == "ai" and booking.notes == "Хочет обсудить обучение отдела продаж"


@pytest.mark.asyncio
async def test_время_заняли_пока_клиент_читал(tg, db, employee, service, workday, client, fake_ai):
    from app.bot import services as bot_services

    _bot, _dp, session = tg
    fake_ai.call(
        "propose_booking", service_id=service.id, employee_id=employee.id,
        date=workday.isoformat(), time="10:00", summary="Диагностика процесса",
    )
    fake_ai.say("Предлагаю понедельник, 10:00.")
    await _feed(tg, _message("на понедельник в 10"))
    bot_services.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    session.clear()

    await _feed(tg, _callback(AiBookCB(action="yes").pack()))
    assert any(texts.SLOT_TAKEN in text for text in session.sent())
    assert "09:00" in session.buttons() and "10:00" not in session.buttons()
    session.clear()

    await _feed(tg, _callback(SlotCB(hour=9, minute=0).pack()))
    await _feed(tg, _callback(ConfirmCB(action="yes").pack()))
    mine = [b for b in _bookings(db) if b.client_id != client.id]
    assert len(mine) == 1 and mine[0].source == "ai" and mine[0].notes == "Диагностика процесса"


@pytest.mark.asyncio
async def test_другое_время_открывает_календарь(tg, db, employee, service, workday, fake_ai):
    _bot, _dp, session = tg
    fake_ai.call(
        "propose_booking", service_id=service.id, employee_id=employee.id,
        date=workday.isoformat(), time="11:00", summary="",
    )
    fake_ai.say("Вот вариант.")
    await _feed(tg, _message("запишите к Иванову"))
    session.clear()

    await _feed(tg, _callback(AiBookCB(action="other").pack()))
    assert texts.AI_OTHER_DAY.format(employee="Иванов", service="Консультация") in session.sent()


@pytest.mark.asyncio
async def test_мои_записи_через_консультанта(tg, db, employee, service, workday, fake_ai):
    from app.bot import services as bot_services

    client = bot_services.get_or_create_client(db, TG_USER_ID, "petr", "Пётр")
    bot_services.create(db, client_id=client["id"], employee_id=employee.id, service_id=service.id, day=workday, slot="15:00")
    _bot, _dp, session = tg
    fake_ai.call("my_bookings")
    fake_ai.say("Вот ваши записи.")

    await _feed(tg, _message("когда я записан?"))
    assert "Вот ваши записи." in session.sent()
    assert texts.BTN_CANCEL_BOOKING in session.buttons()


@pytest.mark.asyncio
async def test_сбой_модели_без_записи(tg, db, employee, service, fake_ai):
    _bot, _dp, session = tg
    await _feed(tg, _message("хочу записаться"))
    assert texts.AI_FAILED in session.sent()
    assert _bookings(db) == []


@pytest.mark.asyncio
async def test_устаревшая_карточка_не_записывает(tg, db, fake_ai):
    _bot, _dp, session = tg
    await _feed(tg, _callback(AiBookCB(action="yes").pack()))
    answers = [m for m in session.calls if isinstance(m, AnswerCallbackQuery)]
    assert answers and answers[-1].text == texts.AI_PROPOSAL_EXPIRED
    assert _bookings(db) == []


@pytest.mark.asyncio
async def test_кнопки_и_команды_не_уходят_в_ai(tg, db, fake_ai):
    _bot, _dp, session = tg
    await _feed(tg, _message(texts.BTN_BOOK))
    await _feed(tg, _message("/неизвестная"))
    assert fake_ai.requests == []
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
