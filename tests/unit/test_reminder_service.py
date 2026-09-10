"""Кому и когда напоминать: окна, исключения, настройки."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKED, CANCELLED
from app.services import reminder_service
from app.services.app_settings_service import REMINDERS, set_setting
from app.services.schedule_service import local_tz

NOW = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def tg_client(db):
    row = Client(name="Пётр", telegram_user_id=555000111)
    db.add(row)
    db.commit()
    return row


def _booking(db, employee, service, client, *, start, created=None, status=BOOKED):
    row = Booking(
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=status,
        source="telegram",
        created_at=created or start - timedelta(days=3),
    )
    db.add(row)
    db.commit()
    return row


def _kinds(db, now=NOW):
    return [item["kind"] for item in reminder_service.due_reminders(db, now)]


# ==== Окна ====

def test_за_сутки_в_начале_окна(db, employee, service, tg_client):
    start = NOW + timedelta(hours=24)
    booking = _booking(db, employee, service, tg_client, start=start)

    [item] = reminder_service.due_reminders(db, NOW)
    assert item["kind"] == "reminder_24h" and item["booking_id"] == booking.id
    assert item["chat_id"] == 555000111
    assert item["start"] == start.astimezone(local_tz()).strftime("%H:%M")
    assert item["employee"] == "Иванов" and item["service"] == "Консультация"


def test_раньше_окна_не_напоминаем(db, employee, service, tg_client):
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=24, minutes=1))
    assert _kinds(db) == []


def test_после_окна_напоминание_за_сутки_уже_не_шлём(db, employee, service, tg_client):
    """До визита 23 ч 29 мин: окно «за сутки» закрылось, а до «за час» далеко."""
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=23, minutes=29))
    assert _kinds(db) == []


def test_за_час(db, employee, service, tg_client):
    _booking(db, employee, service, tg_client, start=NOW + timedelta(minutes=50))
    assert _kinds(db) == ["reminder_1h"]


# ==== Исключения ====

def test_отменённая_не_напоминается(db, employee, service, tg_client):
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=24), status=CANCELLED)
    assert _kinds(db) == []


def test_запись_после_момента_напоминания_не_напоминается(db, employee, service, tg_client):
    """Визит через 50 минут, момент «за час» был 10 минут назад, а записались 5 минут назад."""
    _booking(
        db, employee, service, tg_client,
        start=NOW + timedelta(minutes=50),
        created=NOW - timedelta(minutes=5),
    )
    assert _kinds(db) == []


def test_клиент_без_telegram_пропускается(db, employee, service, client):
    _booking(db, employee, service, client, start=NOW + timedelta(hours=24))
    assert _kinds(db) == []


def test_отправленное_не_повторяется(db, employee, service, tg_client):
    booking = _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=24))
    assert reminder_service.mark_sent(db, booking.id, "reminder_24h") is True
    assert _kinds(db) == []
    assert reminder_service.mark_sent(db, booking.id, "reminder_24h") is False


def test_напоминание_за_сутки_не_мешает_напоминанию_за_час(db, employee, service, tg_client):
    booking = _booking(db, employee, service, tg_client, start=NOW + timedelta(minutes=50))
    reminder_service.mark_sent(db, booking.id, "reminder_24h")
    assert _kinds(db) == ["reminder_1h"]


# ==== Настройки ====

def test_часы_берутся_из_настроек(db, employee, service, tg_client):
    set_setting(db, REMINDERS, {"enabled": True, "hours_before": [3]})
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=3))
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=24))
    assert _kinds(db) == ["reminder_3h"]


def test_выключенные_напоминания_не_шлются(db, employee, service, tg_client):
    set_setting(db, REMINDERS, {"enabled": False, "hours_before": [24, 1]})
    _booking(db, employee, service, tg_client, start=NOW + timedelta(hours=24))
    assert _kinds(db) == []


def test_мусор_в_настройках_не_роняет_расчёт(db, employee, service, tg_client):
    set_setting(db, REMINDERS, {"enabled": True, "hours_before": ["abc", 0, 999, 1]})
    assert reminder_service.reminder_hours(db) == [1]


# ==== Разбор формы ====

@pytest.mark.parametrize(
    "raw, expected",
    [("24, 1", [24, 1]), ("1;24", [24, 1]), (" 2 ", [2]), ("24,24,1", [24, 1])],
)
def test_разбор_часов(raw, expected):
    assert reminder_service.parse_hours(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ,  ", "0", "169", "час", "1,2,3,4"])
def test_неверные_часы_отклоняются_с_понятным_текстом(raw):
    with pytest.raises(ValueError) as exc:
        reminder_service.parse_hours(raw)
    assert str(exc.value)
