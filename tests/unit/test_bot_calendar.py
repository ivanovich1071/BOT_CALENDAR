"""Inline-календарь бота."""

from datetime import date

from app.bot.keyboards.callbacks import CalendarCB
from app.bot.keyboards.calendar import build_calendar, is_selectable, month_matrix, shift_month

WORKDAYS = {0, 1, 2, 3, 4}


def _texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_сетка_месяца_содержит_только_свои_дни():
    weeks = month_matrix(2026, 9)
    days = [d.day for week in weeks for d in week if d is not None]
    assert days == list(range(1, 31))  # в сентябре 30 дней, чужие месяцы — None


def test_переход_через_границу_года():
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)


def test_выходной_не_выбирается():
    saturday = date(2026, 9, 12)
    assert saturday.weekday() == 5
    assert not is_selectable(saturday, WORKDAYS, date(2026, 9, 1), date(2026, 12, 1))


def test_прошедший_день_не_выбирается():
    assert not is_selectable(date(2026, 9, 8), WORKDAYS, date(2026, 9, 9), date(2026, 12, 1))


def test_день_за_горизонтом_не_выбирается():
    assert not is_selectable(date(2027, 1, 4), WORKDAYS, date(2026, 9, 9), date(2026, 12, 8))


def test_рабочий_день_внутри_горизонта_выбирается():
    thursday = date(2026, 9, 10)
    assert is_selectable(thursday, WORKDAYS, date(2026, 9, 9), date(2026, 12, 8))


def test_недоступные_дни_показаны_точкой():
    markup = build_calendar(
        2026, 9, allowed_weekdays=WORKDAYS, min_date=date(2026, 9, 9), max_date=date(2026, 12, 8)
    )
    texts = _texts(markup)
    assert "10" in texts  # рабочий четверг
    assert "12" not in texts  # суббота
    assert "8" not in texts  # уже прошло
    assert texts.count("·") > 0


def test_стрелка_назад_прячется_на_первом_месяце():
    markup = build_calendar(
        2026, 9, allowed_weekdays=WORKDAYS, min_date=date(2026, 9, 9), max_date=date(2026, 12, 8)
    )
    header = markup.inline_keyboard[0]
    assert header[0].text.strip() == ""
    assert CalendarCB.unpack(header[0].callback_data).action == "ignore"
    assert header[2].text == "›"


def test_стрелка_вперёд_прячется_на_последнем_месяце():
    markup = build_calendar(
        2026, 12, allowed_weekdays=WORKDAYS, min_date=date(2026, 9, 9), max_date=date(2026, 12, 8)
    )
    header = markup.inline_keyboard[0]
    assert header[0].text == "‹"
    assert header[2].text.strip() == ""


def test_нажатие_на_день_несёт_дату():
    markup = build_calendar(
        2026, 9, allowed_weekdays=WORKDAYS, min_date=date(2026, 9, 9), max_date=date(2026, 12, 8)
    )
    for row in markup.inline_keyboard:
        for button in row:
            if button.text == "10":
                data = CalendarCB.unpack(button.callback_data)
                assert (data.action, data.year, data.month, data.day) == ("day", 2026, 9, 10)
                return
    raise AssertionError("кнопка с 10 числом не найдена")
