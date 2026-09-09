"""Расписание и расчёт свободных слотов."""

from datetime import datetime, time, timedelta, timezone

from app.services.schedule_service import free_slots, local_tz, working_intervals


def _at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=local_tz())


def _times(slots):
    return [s.strftime("%H:%M") for s in slots]


def test_рабочие_интервалы_разрезаются_перерывом(db, employee, workday):
    intervals = working_intervals(db, employee.id, workday)
    assert [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in intervals] == [
        ("09:00", "13:00"),
        ("14:00", "18:00"),
    ]


def test_выходной_без_слотов(db, employee, workday):
    saturday = workday + timedelta(days=5)
    assert saturday.weekday() == 5
    assert free_slots(db, employee.id, 60, saturday) == []


def test_услуга_60_минут_не_залезает_на_перерыв(db, employee, workday):
    times = _times(free_slots(db, employee.id, 60, workday))
    assert times[0] == "09:00"
    assert "12:00" in times      # 12:00–13:00 упирается в перерыв ровно
    assert "12:30" not in times  # 12:30–13:30 пересекает перерыв
    assert "13:00" not in times
    assert times[-1] == "17:00"  # 17:00–18:00 — последний целый слот


def test_услуга_90_минут_укорачивает_хвосты_интервалов(db, employee, workday):
    times = _times(free_slots(db, employee.id, 90, workday))
    assert times[0] == "09:00"
    assert "11:30" in times      # 11:30–13:00
    assert "12:00" not in times  # 12:00–13:30 не помещается до перерыва
    assert times[-1] == "16:30"  # 16:30–18:00


def test_занятость_google_убирает_пересекающиеся_слоты(db, employee, workday):
    busy_start = _at(workday, 11, 0).astimezone(timezone.utc)
    busy = [(busy_start, busy_start + timedelta(hours=1))]
    times = _times(free_slots(db, employee.id, 60, workday, busy))
    assert "10:30" not in times  # 10:30–11:30 пересекает занятость
    assert "11:00" not in times
    assert "12:00" in times


def test_прошедшее_время_не_предлагается(db, employee, workday):
    """Слоты вчерашнего дня не выдаются, даже если день рабочий."""
    past = workday - timedelta(days=7)
    assert free_slots(db, employee.id, 60, past) == []
