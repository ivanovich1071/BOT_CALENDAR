"""Настройки записи: шаг сетки, минимум до начала, горизонт; форма в админке."""

from datetime import timedelta

from app.bot import services as bot
from app.services.app_settings_service import BOOKING, GOOGLE_SYNC, get_setting, set_setting
from app.services.schedule_service import free_slots, local_now


def _times(slots) -> list[str]:
    return [s.strftime("%H:%M") for s in slots]


def test_по_умолчанию_шаг_30_минут(db, employee, workday):
    assert _times(free_slots(db, employee.id, 60, workday))[:3] == ["09:00", "09:30", "10:00"]


def test_шаг_сетки_из_настроек(db, employee, workday):
    set_setting(db, BOOKING, {"slot_step_minutes": 60, "horizon_days": 90, "min_lead_minutes": 0})
    assert _times(free_slots(db, employee.id, 60, workday)) == ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00"]


def test_минимум_до_начала(db, employee, workday):
    set_setting(db, BOOKING, {"slot_step_minutes": 30, "horizon_days": 90, "min_lead_minutes": 14 * 24 * 60})
    assert free_slots(db, employee.id, 60, workday) == []


def test_горизонт_из_настроек(db):
    today = local_now().date()
    assert bot.horizon()[1] == today + timedelta(days=bot.BOOKING_HORIZON_DAYS)
    set_setting(db, BOOKING, {"slot_step_minutes": 30, "horizon_days": 10, "min_lead_minutes": 0})
    assert bot.horizon(db)[1] == today + timedelta(days=10)


def test_форма_настроек_записи(admin_client, db):
    page = admin_client.get("/admin/settings")
    assert "Шаг сетки" in page.text

    ok = admin_client.post(
        "/admin/settings/booking",
        data={"slot_step": "15", "horizon_days": "60", "min_lead": "120", "sync_minutes": "5"},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and "ok=" in ok.headers["location"]
    assert get_setting(db, BOOKING) == {"slot_step_minutes": 15, "horizon_days": 60, "min_lead_minutes": 120}
    assert get_setting(db, GOOGLE_SYNC)["interval_minutes"] == 5

    bad = admin_client.post(
        "/admin/settings/booking",
        data={"slot_step": "1", "horizon_days": "60", "min_lead": "0", "sync_minutes": "5"},
        follow_redirects=False,
    )
    assert "err=" in bad.headers["location"]
    assert get_setting(db, BOOKING)["slot_step_minutes"] == 15
