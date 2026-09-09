"""Состояния диалогов бота."""

from aiogram.fsm.state import State, StatesGroup


class Booking(StatesGroup):
    """Запись: услуга → специалист → дата → время → подтверждение."""

    service = State()
    employee = State()
    day = State()
    slot = State()
    confirm = State()


class Reschedule(StatesGroup):
    """Перенос существующей записи: дата → время → подтверждение."""

    day = State()
    slot = State()
    confirm = State()


class Profile(StatesGroup):
    phone = State()
