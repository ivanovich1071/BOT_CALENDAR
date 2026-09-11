"""Инструменты консультанта: поиск времени, карточка записи, записи клиента."""

from sqlalchemy import func, select

from app.ai import tools
from app.bot import services as bot
from app.models.booking import Booking
from app.models.service import Service


def _ctx(client_id: int = 0) -> tools.ToolContext:
    return tools.ToolContext(client_id=client_id)


def test_время_специалиста_на_день(db, employee, service, workday):
    result = tools.find_slots(db, _ctx(), {"service_id": service.id, "date_from": workday.isoformat()})
    assert result["slots"][0] == {
        "employee_id": employee.id,
        "employee": "Иванов",
        "date": workday.isoformat(),
        "weekday": "пн",
        "time": "09:00",
    }
    assert len(result["slots"]) == tools.SLOTS_PER_EMPLOYEE_DAY


def test_окно_времени_учитывается(db, employee, service, workday):
    result = tools.find_slots(
        db, _ctx(), {"service_id": service.id, "date_from": workday.isoformat(), "time_from": "14:00", "time_to": "17:00"}
    )
    assert [s["time"] for s in result["slots"]] == ["14:00", "14:30", "15:00"]


def test_незнакомая_услуга(db, employee, service, workday):
    result = tools.find_slots(db, _ctx(), {"service_id": 999, "date_from": workday.isoformat()})
    assert "error" in result


def test_специалист_без_этой_услуги(db, employee, service, workday):
    other = Service(name="Диагностика", duration_minutes=60)
    db.add(other)
    db.commit()
    employee.services = [other]
    db.commit()

    by_employee = tools.find_slots(
        db, _ctx(), {"service_id": service.id, "employee_id": employee.id, "date_from": workday.isoformat()}
    )
    anyone = tools.find_slots(db, _ctx(), {"service_id": service.id, "date_from": workday.isoformat()})

    assert "error" in by_employee
    assert anyone["slots"] == [] and "note" in anyone


def test_карточка_готовится_но_запись_не_создаётся(db, client, employee, service, workday):
    ctx = _ctx(client.id)
    result = tools.propose_booking(
        db,
        ctx,
        {
            "service_id": service.id,
            "employee_id": employee.id,
            "date": workday.isoformat(),
            "time": "10:00",
            "summary": "Хочет обсудить обучение отдела продаж",
        },
    )
    assert result["ok"] is True
    assert (ctx.proposal.slot, ctx.proposal.end, ctx.proposal.employee) == ("10:00", "11:00", "Иванов")
    assert ctx.proposal.summary == "Хочет обсудить обучение отдела продаж"
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


def test_занятое_время_карточку_не_даёт(db, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    ctx = _ctx(client.id)
    result = tools.propose_booking(
        db,
        ctx,
        {"service_id": service.id, "employee_id": employee.id, "date": workday.isoformat(), "time": "10:00", "summary": ""},
    )
    assert result["ok"] is False and ctx.proposal is None


def test_мои_записи_включают_карточки(db, client, employee, service, workday):
    bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="11:00")
    ctx = _ctx(client.id)
    result = tools.my_bookings(db, ctx, {})
    assert ctx.show_bookings is True
    assert result["bookings"][0]["start"] == "11:00"


def test_неизвестный_инструмент(db):
    assert "error" in tools.run_tool(db, _ctx(), "drop_database", {})
