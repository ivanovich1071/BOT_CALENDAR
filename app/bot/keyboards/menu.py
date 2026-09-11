"""Клавиатуры главного меню и типовые наборы кнопок."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot import texts
from app.bot.keyboards.callbacks import BookingCB, ConfirmCB, EmployeeCB, ServiceCB, SlotCB

# Слотов в строке: три помещаются на экран телефона без переноса
SLOTS_PER_ROW = 3


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_BOOK), KeyboardButton(text=texts.BTN_MY)],
            [KeyboardButton(text=texts.BTN_INFO)],
        ],
        resize_keyboard=True,
    )


def ask_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_PHONE, request_contact=True)],
            [KeyboardButton(text=texts.BTN_SKIP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def services(items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for s in items:
        price = f" · {s['price_label']}" if s.get("price_label") else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{s['name']} · {s['duration']} мин{price}",
                    callback_data=ServiceCB(service_id=s["id"]).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def employees(items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for e in items:
        title = e["name"] + (f" · {e['specialization']}" if e.get("specialization") else "")
        rows.append(
            [InlineKeyboardButton(text=title, callback_data=EmployeeCB(employee_id=e["id"]).pack())]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _slot_cb(value: str) -> str:
    hour, minute = value.split(":")
    return SlotCB(hour=int(hour), minute=int(minute)).pack()


def slots(times: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(times), SLOTS_PER_ROW):
        rows.append(
            [
                InlineKeyboardButton(text=t, callback_data=_slot_cb(t))
                for t in times[i : i + SLOTS_PER_ROW]
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_YES, callback_data=ConfirmCB(action="yes").pack())],
            [InlineKeyboardButton(text=texts.BTN_NO, callback_data=ConfirmCB(action="no").pack())],
        ]
    )


def booking_actions(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_RESCHEDULE,
                    callback_data=BookingCB(action="reschedule", booking_id=booking_id).pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_CANCEL_BOOKING,
                    callback_data=BookingCB(action="cancel", booking_id=booking_id).pack(),
                ),
            ]
        ]
    )


def confirm_cancel(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Да, отменить",
                    callback_data=BookingCB(action="cancel_yes", booking_id=booking_id).pack(),
                )
            ],
            [InlineKeyboardButton(text=texts.BTN_NO, callback_data=ConfirmCB(action="no").pack())],
        ]
    )
