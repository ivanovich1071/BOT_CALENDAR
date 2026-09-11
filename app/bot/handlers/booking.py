"""Запись: услуга → специалист → дата → время → подтверждение."""

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import services, texts
from app.bot.db import run_db
from app.bot.deps import current_client
from app.bot.keyboards.calendar import build_calendar
from app.bot.keyboards.callbacks import CalendarCB, ConfirmCB, EmployeeCB, ServiceCB, SlotCB
from app.bot.keyboards.menu import main_menu
from app.bot.keyboards import menu
from app.bot.states import Booking
from app.services import booking_service

logger = logging.getLogger(__name__)

router = Router(name="booking")


async def calendar_markup(employee_id: int, year: int, month: int):
    """Календарь месяца: нажимаются только дни, когда специалист принимает."""
    min_date, max_date = services.horizon()
    days = await run_db(services.open_days, employee_id, min_date, max_date)
    return build_calendar(year, month, min_date=min_date, max_date=max_date, allowed_days=days)


async def show_calendar(target: Message, employee_id: int, year: int, month: int) -> None:
    await target.answer(texts.CHOOSE_DAY, reply_markup=await calendar_markup(employee_id, year, month))


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    items = await run_db(services.active_services)
    if not items:
        await message.answer(texts.NO_SERVICES, reply_markup=main_menu())
        return
    await state.set_state(Booking.service)
    await message.answer(texts.CHOOSE_SERVICE, reply_markup=menu.services(items))


@router.callback_query(Booking.service, ServiceCB.filter())
async def pick_service(call: CallbackQuery, callback_data: ServiceCB, state: FSMContext) -> None:
    items = await run_db(services.active_services)
    chosen = next((s for s in items if s["id"] == callback_data.service_id), None)
    if chosen is None:
        await call.answer(texts.SOMETHING_WRONG, show_alert=True)
        return
    await state.update_data(service_id=chosen["id"], service_name=chosen["name"])

    employees = await run_db(services.employees_with_schedule, chosen["id"])
    if not employees:
        await state.clear()
        await call.message.edit_text(texts.NO_EMPLOYEES)
        await call.answer()
        return

    await state.set_state(Booking.employee)
    await call.message.edit_text(texts.CHOOSE_EMPLOYEE, reply_markup=menu.employees(employees))
    await call.answer()


@router.callback_query(Booking.employee, EmployeeCB.filter())
async def pick_employee(call: CallbackQuery, callback_data: EmployeeCB, state: FSMContext) -> None:
    data = await state.get_data()
    employees = await run_db(services.employees_with_schedule, data.get("service_id"))
    chosen = next((e for e in employees if e["id"] == callback_data.employee_id), None)
    if chosen is None:
        await call.answer(texts.SOMETHING_WRONG, show_alert=True)
        return
    await state.update_data(employee_id=chosen["id"], employee_name=chosen["name"])
    await state.set_state(Booking.day)

    today, _ = services.horizon()
    await call.message.delete()
    await show_calendar(call.message, chosen["id"], today.year, today.month)
    await call.answer()


@router.callback_query(CalendarCB.filter(F.action == "ignore"))
async def calendar_noop(call: CallbackQuery) -> None:
    """Нерабочий день или подпись — просто гасим «часики» на кнопке."""
    await call.answer()


@router.callback_query(Booking.day, CalendarCB.filter(F.action.in_({"prev", "next"})))
async def flip_month(call: CallbackQuery, callback_data: CalendarCB, state: FSMContext) -> None:
    data = await state.get_data()
    await call.message.edit_reply_markup(
        reply_markup=await calendar_markup(data["employee_id"], callback_data.year, callback_data.month)
    )
    await call.answer()


@router.callback_query(Booking.day, CalendarCB.filter(F.action == "day"))
async def pick_day(call: CallbackQuery, callback_data: CalendarCB, state: FSMContext) -> None:
    data = await state.get_data()
    day = date(callback_data.year, callback_data.month, callback_data.day)
    times = await run_db(services.free_times, data["employee_id"], data["service_id"], day)
    if not times:
        await call.answer(texts.NO_SLOTS.format(date=texts.human_date(day)), show_alert=True)
        return

    await state.update_data(day=day.isoformat())
    await state.set_state(Booking.slot)
    await call.message.edit_text(
        texts.CHOOSE_SLOT.format(date=texts.human_date(day)), reply_markup=menu.slots(times)
    )
    await call.answer()


@router.callback_query(Booking.slot, SlotCB.filter())
async def pick_slot(call: CallbackQuery, callback_data: SlotCB, state: FSMContext) -> None:
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    slot = callback_data.value
    items = await run_db(services.active_services)
    duration = next((s["duration"] for s in items if s["id"] == data["service_id"]), 60)
    finish = services.parse_slot(day, slot) + timedelta(minutes=duration)

    await state.update_data(slot=slot)
    await state.set_state(Booking.confirm)
    await call.message.edit_text(
        texts.CONFIRM.format(
            employee=data["employee_name"],
            service=data["service_name"],
            date=texts.human_date(day),
            start=slot,
            end=finish.strftime("%H:%M"),
        ),
        reply_markup=menu.confirm(),
    )
    await call.answer()


@router.callback_query(Booking.confirm, ConfirmCB.filter(F.action == "yes"))
async def confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    client = await current_client(call.from_user)
    day = date.fromisoformat(data["day"])
    try:
        card = await run_db(
            services.create,
            client_id=client["id"],
            employee_id=data["employee_id"],
            service_id=data["service_id"],
            day=day,
            slot=data["slot"],
        )
    except booking_service.SlotTakenError:
        times = await run_db(services.free_times, data["employee_id"], data["service_id"], day)
        await state.set_state(Booking.slot)
        await call.message.edit_text(
            texts.SLOT_TAKEN + "\n\n" + texts.CHOOSE_SLOT.format(date=texts.human_date(day)),
            reply_markup=menu.slots(times),
        )
        await call.answer()
        return
    except Exception:  # noqa: BLE001 — клиенту нужен внятный ответ, а не молчание
        logger.exception("Не удалось создать запись из бота")
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


@router.callback_query(ConfirmCB.filter(F.action == "no"))
async def abort(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(texts.CANCELLED_ACTION)
    await call.answer()
