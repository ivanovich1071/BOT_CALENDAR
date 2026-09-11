"""Свободный текст: ИИ-консультант отвечает по базе знаний и помогает записаться.

Роутер подключается последним — сюда доходит только текст, который не поймали
кнопки и команды. Модель ничего не записывает сама: она предлагает карточку,
а запись создаёт нажатие «Записаться» — через тот же services.create, что и кнопки.
"""

import logging
from contextlib import suppress
from datetime import date

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.ai import agent
from app.ai.tools import Proposal
from app.bot import services, texts
from app.bot.db import run_db
from app.bot.deps import current_client
from app.bot.formatting import to_telegram_html
from app.bot.handlers import appointments
from app.bot.handlers.booking import calendar_markup
from app.bot.keyboards import menu
from app.bot.keyboards.callbacks import AiBookCB
from app.bot.keyboards.menu import main_menu
from app.bot.ratelimit import AI_REQUESTS_PER_HOUR, limiter_for
from app.bot.states import Booking
from app.config.settings import get_settings
from app.models.enums import SOURCE_AI
from app.services import booking_service

logger = logging.getLogger(__name__)

router = Router(name="ai")

PROPOSAL_KEY = "ai_proposal"


def _proposal_text(p: Proposal) -> str:
    return texts.AI_PROPOSAL.format(
        employee=p.employee,
        service=p.service,
        date=texts.human_date(date.fromisoformat(p.day)),
        start=p.slot,
        end=p.end,
    )


def button_context(data: dict) -> str | None:
    """Незаконченный выбор в меню записи — строкой для консультанта, иначе None."""
    parts = []
    if data.get("service_name"):
        parts.append(f"услуга «{data['service_name']}»")
    if data.get("employee_name"):
        parts.append(f"специалист {data['employee_name']}")
    if data.get("day"):
        with suppress(ValueError):
            day = date.fromisoformat(data["day"])
            parts.append(f"день {day.isoformat()} — {texts.human_date(day)}")
    if data.get("slot"):
        parts.append(f"время {data['slot']}")
    return ", ".join(parts) or None


@router.message(F.text, ~F.text.startswith("/"))
async def free_text(message: Message, state: FSMContext) -> None:
    if not get_settings().openrouter_api_key:
        await message.answer(texts.AI_DISABLED, reply_markup=main_menu())
        return
    config = await run_db(agent.ai_config)
    if not limiter_for(int(config.get("hourly_limit") or AI_REQUESTS_PER_HOUR)).allow(message.from_user.id):
        await message.answer(texts.AI_RATE_LIMITED, reply_markup=main_menu())
        return

    client = await current_client(message.from_user)
    # Текст посреди записи кнопками: разговор ведёт консультант, но выбранное в меню он знает
    context = button_context(await state.get_data())
    await state.clear()
    with suppress(TelegramAPIError):
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    reply = await agent.respond(client["id"], message.text, button_context=context)
    if reply.failed:
        await message.answer(texts.AI_FAILED, reply_markup=main_menu())
        return

    await message.answer(to_telegram_html(reply.text), reply_markup=main_menu())
    if reply.proposal:
        await state.update_data(**{PROPOSAL_KEY: reply.proposal.to_dict()})
        await message.answer(_proposal_text(reply.proposal), reply_markup=menu.ai_proposal())
    if reply.show_bookings:
        await appointments.send_booking_cards(message, client["id"], empty_message=False)


async def _proposal(call: CallbackQuery, state: FSMContext) -> Proposal | None:
    raw = (await state.get_data()).get(PROPOSAL_KEY)
    if not raw:
        await call.answer(texts.AI_PROPOSAL_EXPIRED, show_alert=True)
        return None
    return Proposal.from_dict(raw)


@router.callback_query(AiBookCB.filter(F.action == "yes"))
async def book_proposal(call: CallbackQuery, state: FSMContext) -> None:
    p = await _proposal(call, state)
    if p is None:
        return
    day = date.fromisoformat(p.day)
    client = await current_client(call.from_user)
    try:
        card = await run_db(
            services.create,
            client_id=client["id"],
            employee_id=p.employee_id,
            service_id=p.service_id,
            day=day,
            slot=p.slot,
            source=SOURCE_AI,
            notes=p.summary or None,
        )
    except booking_service.SlotTakenError:
        # Пока клиент читал, время заняли — дальше обычным выбором, заметка сохраняется
        await state.clear()
        await state.update_data(
            service_id=p.service_id,
            service_name=p.service,
            employee_id=p.employee_id,
            employee_name=p.employee,
            day=p.day,
            source=SOURCE_AI,
            notes=p.summary or None,
        )
        times = await run_db(services.free_times, p.employee_id, p.service_id, day)
        if times:
            await state.set_state(Booking.slot)
            await call.message.edit_text(
                texts.SLOT_TAKEN + "\n\n" + texts.CHOOSE_SLOT.format(date=texts.human_date(day)),
                reply_markup=menu.slots(times),
            )
        else:
            await state.set_state(Booking.day)
            await call.message.edit_text(
                texts.SLOT_TAKEN, reply_markup=await calendar_markup(p.employee_id, day.year, day.month)
            )
        await call.answer()
        return
    except booking_service.NotFoundError:
        await state.clear()
        await call.answer(texts.AI_PROPOSAL_EXPIRED, show_alert=True)
        return
    except Exception:  # noqa: BLE001 — клиенту нужен внятный ответ, а не молчание
        logger.exception("Не удалось создать запись из карточки ИИ")
        await state.clear()
        await call.message.edit_text(texts.BOOKING_FAILED)
        await call.answer()
        return

    await state.clear()
    await call.message.edit_text(
        texts.BOOKED.format(
            employee=card["employee"],
            service=card["service"],
            date=texts.human_date(card["date"]),
            start=card["start"],
            end=card["end"],
        )
    )
    await call.answer()


@router.callback_query(AiBookCB.filter(F.action == "other"))
async def other_time(call: CallbackQuery, state: FSMContext) -> None:
    p = await _proposal(call, state)
    if p is None:
        return
    await state.clear()
    await state.set_state(Booking.day)
    await state.update_data(
        service_id=p.service_id,
        service_name=p.service,
        employee_id=p.employee_id,
        employee_name=p.employee,
        source=SOURCE_AI,
        notes=p.summary or None,
    )
    today, _ = services.horizon()
    await call.message.edit_text(
        texts.AI_OTHER_DAY.format(employee=p.employee, service=p.service),
        reply_markup=await calendar_markup(p.employee_id, today.year, today.month),
    )
    await call.answer()
