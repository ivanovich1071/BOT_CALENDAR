"""Свободный текст: AI разбирает фразу и ведёт клиента в обычный сценарий записи.

Роутер подключается последним — сюда доходит только текст, который не поймали
кнопки и команды. Модель ничего не записывает сама: она заполняет поля того же
диалога, а время клиент выбирает и подтверждает кнопками. Второго пути
бронирования не появляется.
"""

import datetime as dt
import logging
from contextlib import suppress

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.ai import intent as ai_intent
from app.ai.resolve import filter_times, match_name
from app.bot import services, texts
from app.bot.db import run_db
from app.bot.handlers import appointments, start
from app.bot.handlers.booking import show_calendar
from app.bot.keyboards import menu
from app.bot.keyboards.menu import main_menu
from app.bot.ratelimit import ai_limiter
from app.bot.states import Booking
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

router = Router(name="ai")


@router.message(F.text, ~F.text.startswith("/"))
async def free_text(message: Message, state: FSMContext) -> None:
    if not get_settings().openrouter_api_key:
        await message.answer(texts.AI_DISABLED, reply_markup=main_menu())
        return
    if not ai_limiter.allow(message.from_user.id):
        await message.answer(texts.AI_RATE_LIMITED, reply_markup=main_menu())
        return

    service_items = await run_db(services.active_services)
    employee_items = await run_db(services.employees_with_schedule)
    today, last_day = services.horizon()

    with suppress(TelegramAPIError):
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    parsed = await ai_intent.parse(
        message.text,
        [s["name"] for s in service_items],
        [e["name"] for e in employee_items],
        today,
    )
    logger.info("AI: намерение %s", parsed.action)

    if parsed.action == "my_bookings":
        await appointments.my_bookings(message, state)
    elif parsed.action == "info":
        await start.info(message)
    elif parsed.action == "book":
        await _continue_booking(message, state, parsed, service_items, employee_items, today, last_day)
    else:
        await message.answer(texts.AI_NOT_UNDERSTOOD, reply_markup=main_menu())


def _pick(name: str | None, items: list[dict]) -> dict | None:
    """Названное клиентом — или единственный вариант, если выбирать не из чего."""
    return match_name(name, items) or (items[0] if len(items) == 1 else None)


async def _continue_booking(
    message: Message,
    state: FSMContext,
    parsed: ai_intent.Intent,
    service_items: list[dict],
    employee_items: list[dict],
    today: dt.date,
    last_day: dt.date,
) -> None:
    """Кладёт распознанное в состояние записи и открывает первый недостающий шаг."""
    await state.clear()
    if not service_items:
        await message.answer(texts.NO_SERVICES, reply_markup=main_menu())
        return

    service = _pick(parsed.service, service_items)
    if service is None:
        await state.set_state(Booking.service)
        await message.answer(texts.AI_CHOOSE_SERVICE, reply_markup=menu.services(service_items))
        return
    await state.update_data(service_id=service["id"], service_name=service["name"])

    if not employee_items:
        await state.clear()
        await message.answer(texts.NO_EMPLOYEES, reply_markup=main_menu())
        return
    employee = _pick(parsed.employee, employee_items)
    if employee is None:
        await state.set_state(Booking.employee)
        await message.answer(texts.CHOOSE_EMPLOYEE, reply_markup=menu.employees(employee_items))
        return
    await state.update_data(employee_id=employee["id"], employee_name=employee["name"])

    day = parsed.date if parsed.date and today <= parsed.date <= last_day else None
    if day is None:
        await state.set_state(Booking.day)
        await show_calendar(message, employee["id"], today.year, today.month)
        return

    times = await run_db(services.free_times, employee["id"], service["id"], day)
    if not times:
        await state.set_state(Booking.day)
        await message.answer(texts.NO_SLOTS.format(date=texts.human_date(day)))
        await show_calendar(message, employee["id"], day.year, day.month)
        return

    await state.update_data(day=day.isoformat())
    await state.set_state(Booking.slot)
    in_window = filter_times(times, parsed.time_from, parsed.time_to)
    if in_window:
        await message.answer(
            texts.AI_FOUND_SLOTS.format(
                service=service["name"], employee=employee["name"], date=texts.human_date(day)
            ),
            reply_markup=menu.slots(in_window),
        )
    else:
        await message.answer(
            texts.AI_WINDOW_EMPTY.format(date=texts.human_date(day)),
            reply_markup=menu.slots(times),
        )
