"""Мои записи: просмотр, перенос, отмена."""

import logging
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import services, texts
from app.bot.db import run_db
from app.bot.deps import current_client
from app.bot.handlers.booking import calendar_markup
from app.bot.keyboards import menu
from app.bot.keyboards.callbacks import BookingCB, CalendarCB, ConfirmCB, SlotCB
from app.bot.keyboards.menu import main_menu
from app.bot.states import Reschedule
from app.services import booking_service

logger = logging.getLogger(__name__)

router = Router(name="appointments")


@router.message(F.text == texts.BTN_MY)
async def my_bookings(message: Message, state: FSMContext) -> None:
    await state.clear()
    client = await current_client(message.from_user)
    await send_booking_cards(message, client["id"], header=True)


async def send_booking_cards(
    message: Message, client_id: int, *, header: bool = False, empty_message: bool = True
) -> None:
    """Карточки активных записей с кнопками «Перенести» и «Отменить»."""
    items = await run_db(services.my_bookings, client_id)
    if not items:
        if empty_message:
            await message.answer(texts.NO_BOOKINGS, reply_markup=main_menu())
        return

    if header:
        await message.answer(texts.MY_BOOKINGS, reply_markup=main_menu())
    for card in items:
        await message.answer(
            texts.BOOKING_CARD.format(
                employee=card["employee"],
                service=card["service"],
                date=texts.human_date(card["date"]),
                start=card["start"],
                end=card["end"],
            ),
            reply_markup=menu.booking_actions(card["id"]),
        )


# ==== Перенос ====

@router.callback_query(BookingCB.filter(F.action == "reschedule"))
async def start_reschedule(
    call: CallbackQuery, callback_data: BookingCB, state: FSMContext
) -> None:
    client = await current_client(call.from_user)
    try:
        context = await run_db(services.booking_context, callback_data.booking_id, client["id"])
    except (booking_service.NotFoundError, services.NotYours):
        await call.answer(texts.NOT_FOUND, show_alert=True)
        return

    await state.set_state(Reschedule.day)
    await state.update_data(
        booking_id=context["id"],
        employee_id=context["employee_id"],
        service_id=context["service_id"],
        employee_name=context["employee"],
        service_name=context["service"],
    )
    today, _ = services.horizon()
    await call.message.edit_text(
        texts.RESCHEDULE_CHOOSE_DAY.format(
            date=texts.human_date(context["date"]), start=context["start"]
        ),
        reply_markup=await calendar_markup(context["employee_id"], today.year, today.month),
    )
    await call.answer()


@router.callback_query(Reschedule.day, CalendarCB.filter(F.action.in_({"prev", "next"})))
async def flip_month(call: CallbackQuery, callback_data: CalendarCB, state: FSMContext) -> None:
    data = await state.get_data()
    await call.message.edit_reply_markup(
        reply_markup=await calendar_markup(data["employee_id"], callback_data.year, callback_data.month)
    )
    await call.answer()


@router.callback_query(Reschedule.day, CalendarCB.filter(F.action == "day"))
async def pick_day(call: CallbackQuery, callback_data: CalendarCB, state: FSMContext) -> None:
    data = await state.get_data()
    day = date(callback_data.year, callback_data.month, callback_data.day)
    # Своё же событие исключаем из занятости, иначе запись мешает сама себе
    times = await run_db(
        services.free_times,
        data["employee_id"],
        data["service_id"],
        day,
        data["booking_id"],
    )
    if not times:
        await call.answer(texts.NO_SLOTS.format(date=texts.human_date(day)), show_alert=True)
        return

    await state.update_data(day=day.isoformat())
    await state.set_state(Reschedule.slot)
    await call.message.edit_text(
        texts.CHOOSE_SLOT.format(date=texts.human_date(day)), reply_markup=menu.slots(times)
    )
    await call.answer()


@router.callback_query(Reschedule.slot, SlotCB.filter())
async def pick_slot(call: CallbackQuery, callback_data: SlotCB, state: FSMContext) -> None:
    data = await state.get_data()
    client = await current_client(call.from_user)
    day = date.fromisoformat(data["day"])
    try:
        card = await run_db(
            services.reschedule, data["booking_id"], client["id"], day, callback_data.value
        )
    except booking_service.SlotTakenError:
        times = await run_db(
            services.free_times, data["employee_id"], data["service_id"], day, data["booking_id"]
        )
        await call.message.edit_text(
            texts.SLOT_TAKEN + "\n\n" + texts.CHOOSE_SLOT.format(date=texts.human_date(day)),
            reply_markup=menu.slots(times),
        )
        await call.answer()
        return
    except (booking_service.NotFoundError, services.NotYours):
        await state.clear()
        await call.message.edit_text(texts.NOT_FOUND)
        await call.answer()
        return
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось перенести запись из бота")
        await state.clear()
        await call.message.edit_text(texts.SOMETHING_WRONG)
        await call.answer()
        return

    await state.clear()
    await call.message.edit_text(
        texts.RESCHEDULED.format(
            employee=card["employee"],
            service=card["service"],
            date=texts.human_date(card["date"]),
            start=card["start"],
            end=card["end"],
        )
    )
    await call.answer()


# ==== Отмена ====

@router.callback_query(BookingCB.filter(F.action == "cancel"))
async def ask_cancel(call: CallbackQuery, callback_data: BookingCB) -> None:
    client = await current_client(call.from_user)
    try:
        card = await run_db(services.booking_card, callback_data.booking_id, client["id"])
    except (booking_service.NotFoundError, services.NotYours):
        await call.answer(texts.NOT_FOUND, show_alert=True)
        return
    await call.message.edit_text(
        texts.CANCEL_CONFIRM.format(
            employee=card["employee"],
            service=card["service"],
            date=texts.human_date(card["date"]),
            start=card["start"],
        ),
        reply_markup=menu.confirm_cancel(card["id"]),
    )
    await call.answer()


@router.callback_query(BookingCB.filter(F.action == "cancel_yes"))
async def do_cancel(call: CallbackQuery, callback_data: BookingCB, state: FSMContext) -> None:
    client = await current_client(call.from_user)
    try:
        await run_db(services.cancel, callback_data.booking_id, client["id"])
    except (booking_service.NotFoundError, services.NotYours):
        await call.answer(texts.NOT_FOUND, show_alert=True)
        return
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось отменить запись из бота")
        await call.message.edit_text(texts.SOMETHING_WRONG)
        await call.answer()
        return
    await state.clear()
    await call.message.edit_text(texts.CANCELLED)
    await call.answer()


@router.callback_query(ConfirmCB.filter(F.action == "no"))
async def abort(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(texts.CANCELLED_ACTION)
    await call.answer()
