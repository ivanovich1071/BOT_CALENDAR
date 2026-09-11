"""Исключения расписания: выходной, закрытое время, дополнительное окно."""

from datetime import time, timedelta

from app.bot import services as bot
from app.models.enums import EXC_BLOCK, EXC_DAY_OFF, EXC_EXTRA
from app.models.schedule_exception import ScheduleException
from app.services.schedule_service import free_slots, working_intervals


def _add(db, employee, day, kind, start=None, end=None, until=None):
    db.add(
        ScheduleException(
            employee_id=employee.id,
            date_from=day,
            date_to=until or day,
            kind=kind,
            start_time=start,
            end_time=end,
        )
    )
    db.commit()


def _hours(intervals):
    return [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in intervals]


def test_без_исключений_обычное_расписание(db, employee, workday):
    assert _hours(working_intervals(db, employee.id, workday)) == [("09:00", "13:00"), ("14:00", "18:00")]


def test_выходной_убирает_день(db, employee, workday):
    _add(db, employee, workday, EXC_DAY_OFF)
    assert working_intervals(db, employee.id, workday) == []


def test_отпуск_на_диапазон_дат(db, employee, workday):
    _add(db, employee, workday, EXC_DAY_OFF, until=workday + timedelta(days=3))
    assert working_intervals(db, employee.id, workday + timedelta(days=2)) == []
    assert working_intervals(db, employee.id, workday + timedelta(days=4)) != []


def test_закрытое_время_вырезается(db, employee, workday):
    _add(db, employee, workday, EXC_BLOCK, time(10, 0), time(11, 0))
    assert _hours(working_intervals(db, employee.id, workday)) == [
        ("09:00", "10:00"),
        ("11:00", "13:00"),
        ("14:00", "18:00"),
    ]


def test_закрытие_без_часов_как_выходной(db, employee, workday):
    _add(db, employee, workday, EXC_BLOCK)
    assert working_intervals(db, employee.id, workday) == []


def test_дополнительное_окно_в_субботу(db, employee, workday):
    saturday = workday + timedelta(days=5)
    assert working_intervals(db, employee.id, saturday) == []
    _add(db, employee, saturday, EXC_EXTRA, time(10, 0), time(14, 0))
    assert _hours(working_intervals(db, employee.id, saturday)) == [("10:00", "14:00")]


def test_дополнительное_окно_склеивается_с_рабочим_временем(db, employee, workday):
    _add(db, employee, workday, EXC_EXTRA, time(17, 0), time(20, 0))
    assert _hours(working_intervals(db, employee.id, workday)) == [("09:00", "13:00"), ("14:00", "20:00")]


def test_слоты_учитывают_закрытое_время(db, employee, workday):
    _add(db, employee, workday, EXC_BLOCK, time(9, 0), time(13, 0))
    slots = free_slots(db, employee.id, 60, workday)
    assert slots[0].strftime("%H:%M") == "14:00"


def test_календарь_бота_видит_выходные_и_доп_окна(db, employee, workday):
    saturday = workday + timedelta(days=5)
    _add(db, employee, workday, EXC_DAY_OFF)
    _add(db, employee, saturday, EXC_EXTRA, time(10, 0), time(14, 0))

    days = bot.open_days(db, employee.id, workday, workday + timedelta(days=6))

    assert workday not in days
    assert workday + timedelta(days=1) in days
    assert saturday in days
    assert workday + timedelta(days=6) not in days  # воскресенье
