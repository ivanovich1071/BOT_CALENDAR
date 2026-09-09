"""Данные inline-кнопок. Отдельный модуль, чтобы фабрики не расползались по хендлерам."""

from aiogram.filters.callback_data import CallbackData


class ServiceCB(CallbackData, prefix="svc"):
    service_id: int


class EmployeeCB(CallbackData, prefix="emp"):
    employee_id: int


class CalendarCB(CallbackData, prefix="cal"):
    action: str  # day | prev | next | ignore
    year: int
    month: int
    day: int


class SlotCB(CallbackData, prefix="slot"):
    # Время числами: ":" в aiogram — служебный разделитель callback-данных
    hour: int
    minute: int

    @property
    def value(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


class ConfirmCB(CallbackData, prefix="cfm"):
    action: str  # yes | no


class BookingCB(CallbackData, prefix="bk"):
    action: str  # reschedule | cancel | cancel_yes
    booking_id: int
