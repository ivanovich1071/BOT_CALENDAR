"""Слой данных бота: регистрация клиента, подбор времени, записи и права."""

from datetime import date, timedelta

import pytest

from app.bot import services as bot
from app.models.enums import CANCELLED, SOURCE_TELEGRAM
from app.services import booking_service
from app.services.schedule_service import local_now


# ==== Клиент ====

def test_клиент_создаётся_при_первом_обращении(db):
    client = bot.get_or_create_client(db, 123456789, "ivan", "Иван Иванов")
    assert client["id"] and client["name"] == "Иван Иванов" and client["phone"] is None


def test_повторное_обращение_не_плодит_клиентов(db):
    first = bot.get_or_create_client(db, 123456789, "ivan", "Иван")
    second = bot.get_or_create_client(db, 123456789, "ivan_new", "Иван")
    assert first["id"] == second["id"]


def test_телефон_сохраняется(db):
    client = bot.get_or_create_client(db, 123456789, None, "Иван")
    bot.set_phone(db, client["id"], "+375291234567")
    assert bot.get_or_create_client(db, 123456789, None, "Иван")["phone"] == "+375291234567"


# ==== Справочники ====

def test_услуги_и_специалисты_видны(db, employee, service):
    assert [s["name"] for s in bot.active_services(db)] == ["Консультация"]
    assert [e["name"] for e in bot.employees_with_schedule(db)] == ["Иванов"]


def test_специалист_без_расписания_не_предлагается(db, service):
    """Записаться к тому, у кого нет рабочих дней, невозможно — не показываем его."""
    from app.models.employee import Employee

    db.add(Employee(name="Без расписания"))
    db.commit()
    assert bot.employees_with_schedule(db) == []


def test_рабочие_дни_недели(db, employee):
    assert bot.working_weekdays(db, employee.id) == {0, 1, 2, 3, 4}


def test_свободное_время_в_формате_часы_минуты(db, employee, service, workday):
    times = bot.free_times(db, employee.id, service.id, workday)
    assert times[0] == "09:00"
    assert "13:00" not in times  # перерыв


# ==== Записи ====

@pytest.fixture
def tg_client(db):
    return bot.get_or_create_client(db, 555000111, "client", "Пётр")


def test_запись_создаётся_с_источником_telegram(db, employee, service, workday, tg_client):
    card = bot.create(
        db,
        client_id=tg_client["id"],
        employee_id=employee.id,
        service_id=service.id,
        day=workday,
        slot="10:00",
    )
    assert card["start"] == "10:00" and card["end"] == "11:00"
    assert card["employee"] == "Иванов" and card["service"] == "Консультация"

    from app.models.booking import Booking

    assert db.get(Booking, card["id"]).source == SOURCE_TELEGRAM


def test_занятый_слот_отклоняется(db, employee, service, workday, tg_client):
    bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
               service_id=service.id, day=workday, slot="10:00")
    with pytest.raises(booking_service.SlotTakenError):
        bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
                   service_id=service.id, day=workday, slot="10:00")


def test_мои_записи_показывают_только_будущее(db, employee, service, workday, tg_client):
    bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
               service_id=service.id, day=workday, slot="10:00")
    items = bot.my_bookings(db, tg_client["id"])
    assert len(items) == 1 and items[0]["start"] == "10:00"


def test_отменённая_запись_пропадает_из_моих(db, employee, service, workday, tg_client):
    card = bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
                      service_id=service.id, day=workday, slot="10:00")
    bot.cancel(db, card["id"], tg_client["id"])
    assert bot.my_bookings(db, tg_client["id"]) == []


def test_перенос_меняет_время(db, employee, service, workday, tg_client):
    card = bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
                      service_id=service.id, day=workday, slot="10:00")
    moved = bot.reschedule(db, card["id"], tg_client["id"], workday, "15:00")
    assert moved["start"] == "15:00" and moved["id"] == card["id"]


def test_при_переносе_своя_запись_не_мешает_себе(db, employee, service, workday, tg_client):
    """Слот, занятый самой переносимой записью, должен остаться доступным."""
    card = bot.create(db, client_id=tg_client["id"], employee_id=employee.id,
                      service_id=service.id, day=workday, slot="10:00")
    times = bot.free_times(db, employee.id, service.id, workday, exclude_booking_id=card["id"])
    assert "10:00" in times


# ==== Права ====

def test_чужую_запись_не_посмотреть(db, employee, service, workday, tg_client, client):
    """client из conftest — другой человек; его бронь клиенту бота недоступна."""
    from app.models.enums import SOURCE_ADMIN
    from app.services import booking_flow

    other, _ = booking_flow.create(
        db, client_id=client.id, employee_id=employee.id, service_id=service.id,
        start_at=bot.parse_slot(workday, "11:00"), source=SOURCE_ADMIN, actor="admin",
    )
    with pytest.raises(bot.NotYours):
        bot.booking_card(db, other.id, tg_client["id"])
    with pytest.raises(bot.NotYours):
        bot.cancel(db, other.id, tg_client["id"])


def test_несуществующая_запись(db, tg_client):
    with pytest.raises(booking_service.NotFoundError):
        bot.booking_card(db, 999999, tg_client["id"])


# ==== Горизонт ====

def test_горизонт_записи_начинается_сегодня(db):
    start, end = bot.horizon()
    assert start == local_now().date()
    assert end - start == timedelta(days=bot.BOOKING_HORIZON_DAYS)


def test_разбор_слота_учитывает_локальную_зону(db):
    moment = bot.parse_slot(date(2026, 9, 10), "13:30")
    assert moment.hour == 13 and moment.minute == 30 and moment.tzinfo is not None
