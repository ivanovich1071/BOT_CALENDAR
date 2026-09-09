"""Inline-календарь выбора даты.

Дни, в которые специалист не принимает, и всё, что вне горизонта записи,
показываются точкой и не нажимаются — клиент сразу видит рабочие дни.
"""

import calendar as calendar_module
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.callbacks import CalendarCB

MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

_IGNORE = CalendarCB(action="ignore", year=0, month=0, day=0).pack()


def month_matrix(year: int, month: int) -> list[list[date | None]]:
    """Недели месяца: список строк по 7 ячеек, пустые дни — None."""
    weeks = []
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month):
        weeks.append([day if day.month == month else None for day in week])
    return weeks


def is_selectable(
    day: date | None, allowed_weekdays: set[int], min_date: date, max_date: date
) -> bool:
    if day is None:
        return False
    if day < min_date or day > max_date:
        return False
    return day.weekday() in allowed_weekdays


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def build_calendar(
    year: int,
    month: int,
    *,
    allowed_weekdays: set[int],
    min_date: date,
    max_date: date,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)
    # Листать за пределы горизонта записи незачем — прячем стрелку
    show_prev = date(prev_year, prev_month, calendar_module.monthrange(prev_year, prev_month)[1]) >= min_date
    show_next = date(next_year, next_month, 1) <= max_date

    rows.append(
        [
            InlineKeyboardButton(
                text="‹" if show_prev else " ",
                callback_data=CalendarCB(
                    action="prev" if show_prev else "ignore",
                    year=prev_year, month=prev_month, day=1,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"{MONTHS_RU[month - 1]} {year}", callback_data=_IGNORE
            ),
            InlineKeyboardButton(
                text="›" if show_next else " ",
                callback_data=CalendarCB(
                    action="next" if show_next else "ignore",
                    year=next_year, month=next_month, day=1,
                ).pack(),
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=name, callback_data=_IGNORE) for name in WEEKDAYS_RU])

    for week in month_matrix(year, month):
        row = []
        for day in week:
            if is_selectable(day, allowed_weekdays, min_date, max_date):
                row.append(
                    InlineKeyboardButton(
                        text=str(day.day),
                        callback_data=CalendarCB(
                            action="day", year=day.year, month=day.month, day=day.day
                        ).pack(),
                    )
                )
            else:
                row.append(InlineKeyboardButton(text="·", callback_data=_IGNORE))
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
