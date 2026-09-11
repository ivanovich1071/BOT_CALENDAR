"""Календарь админки: раскладка записей, рабочего времени и занятости Google по сетке."""

from datetime import datetime, time, timedelta, timezone

from app.bot import services as bot
from app.models.booking import Booking
from app.models.enums import BOOKED, COMPLETED, EXC_DAY_OFF
from app.models.schedule_exception import ScheduleException
from app.services import calendar_grid, calendar_service
from app.services.app_settings_service import GOOGLE_SYNC, set_setting
from app.services.schedule_service import local_tz


def _at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=local_tz())


def test_шкала_по_рабочему_дню_и_перерыв(db, employee, workday):
    grid = calendar_grid.day_view(db, workday, [employee])
    assert (grid.start_minute, grid.end_minute) == (9 * 60, 18 * 60)
    column = grid.columns[0]
    # 09–13 и 14–18 от начала шкалы в 09:00
    assert [(s.top, s.height) for s in column.working] == [(0, 240), (300, 240)]
    slots = [f.slot for f in column.free]
    assert slots[0] == "09:00" and "13:00" not in slots and "14:00" in slots
    assert grid.hours[0] == (0, "09:00") and grid.hours[-1] == (540, "18:00")


def test_запись_стоит_на_своём_месте_и_закрывает_время(db, employee, service, client, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    column = calendar_grid.day_view(db, workday, [employee]).columns[0]
    block = column.blocks[0]
    assert (block.top, block.height, block.lanes) == (60, 60, 1)
    slots = {f.slot for f in column.free}
    assert "10:00" not in slots and "10:30" not in slots and "11:00" in slots


def test_выходной_с_пометкой(db, employee, workday):
    db.add(ScheduleException(employee_id=employee.id, date_from=workday, date_to=workday, kind=EXC_DAY_OFF, note="Отпуск"))
    db.commit()
    grid = calendar_grid.day_view(db, workday, [employee])
    column = grid.columns[0]
    assert column.day_off == "Отпуск" and column.working == [] and column.free == []
    assert (grid.start_minute, grid.end_minute) == (9 * 60, 18 * 60)


def test_шкала_растягивается_под_запись_вне_рабочих_часов(db, employee, service, client, workday):
    start = _at(workday, 19, 30)
    db.add(Booking(client_id=client.id, employee_id=employee.id, service_id=service.id,
                   start_at=start, end_at=start + timedelta(hours=1), status=BOOKED, source="admin"))
    db.commit()
    grid = calendar_grid.day_view(db, workday, [employee])
    assert grid.end_minute == 21 * 60
    assert grid.columns[0].blocks[0].top == 19 * 60 + 30 - 9 * 60


def test_пересекающиеся_записи_встают_рядом(db, employee, service, client, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    start = _at(workday, 10, 30)
    db.add(Booking(client_id=client.id, employee_id=employee.id, service_id=service.id,
                   start_at=start, end_at=start + timedelta(hours=1), status=COMPLETED, source="admin"))
    db.commit()
    blocks = calendar_grid.day_view(db, workday, [employee]).columns[0].blocks
    assert sorted(b.lane for b in blocks) == [0, 1] and all(b.lanes == 2 for b in blocks)


def test_чужие_события_google_без_наших_записей(db, employee, service, client, workday, monkeypatch):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")

    def busy(_db, _employee_id, _start, _end):
        # Google склеил событие нашей записи 10–11 с чужим 11–12
        return [(_at(workday, 10).astimezone(timezone.utc), _at(workday, 12).astimezone(timezone.utc))]

    monkeypatch.setattr(calendar_service, "get_busy_intervals", busy)
    column = calendar_grid.day_view(db, workday, [employee]).columns[0]
    assert [(s.top, s.height) for s in column.google_busy] == [(120, 60)]
    assert not {"11:00", "11:30"} & {f.slot for f in column.free}


def test_выключенный_google_не_спрашивается(db, employee, workday, monkeypatch):
    set_setting(db, GOOGLE_SYNC, {"enabled": False, "interval_minutes": 10})

    def boom(*_args):
        raise AssertionError("Google спрошен при выключенном выключателе")

    monkeypatch.setattr(calendar_service, "get_busy_intervals", boom)
    assert calendar_grid.day_view(db, workday, [employee]).columns[0].google_busy == []


def test_неделя_неделя_всех_и_месяц(db, employee, service, client, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")

    week = calendar_grid.week_view(db, workday, employee)
    assert [len(c.blocks) for c in week.columns] == [1, 0, 0, 0, 0, 0, 0]
    assert week.columns[5].day_off == "Не работает"

    (row_employee, cells), = calendar_grid.week_all_view(db, workday, [employee])
    assert row_employee.id == employee.id
    assert len(cells[0].bookings) == 1 and cells[0].day_off is None and cells[6].day_off

    weeks = calendar_grid.month_view(db, workday, [employee])
    assert all(len(w) == 7 for w in weeks) and weeks[0][0].day.weekday() == 0
    cell = next(c for w in weeks for c in w if c.day == workday)
    assert cell.active == 1


def test_заголовки():
    from datetime import date

    assert calendar_grid.day_title(date(2026, 9, 11)) == "Пятница, 11 сентября 2026"
    assert calendar_grid.week_title(date(2026, 9, 7)) == "7–13 сентября 2026"
    assert calendar_grid.week_title(date(2026, 9, 28)) == "28 сентября – 4 октября 2026"
    assert calendar_grid.month_title(date(2026, 9, 11)) == "Сентябрь 2026"
