"""Выключатель Google Calendar: пауза без потери подключений и сверка при включении."""

from datetime import datetime, time, timedelta, timezone

import pytest
from googleapiclient.errors import HttpError

from app.models.booking import Booking
from app.models.calendar import Calendar
from app.models.enums import SOURCE_ADMIN
from app.models.google_account import GoogleAccount
from app.services import booking_flow, calendar_service
from app.services.app_settings_service import GOOGLE_PENDING, GOOGLE_SYNC, get_setting, set_setting
from app.services.schedule_service import local_tz


class _Resp:
    status = 404
    reason = "test"


class _Run:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class RecordingEvents:
    def __init__(self):
        self.inserted: list[str] = []
        self.patched: list[str] = []
        self.deleted: list[str] = []
        self.missing: set[str] = set()

    def insert(self, calendarId, body):  # noqa: N803 — имя параметра задано Google API
        event_id = f"evt-{len(self.inserted) + 1}"
        self.inserted.append(body["extendedProperties"]["private"]["booking_id"])
        return _Run({"id": event_id})

    def patch(self, calendarId, eventId, body):  # noqa: N803
        if eventId in self.missing:
            return _Run(HttpError(_Resp(), b"{}"))
        self.patched.append(eventId)
        return _Run({"id": eventId})

    def delete(self, calendarId, eventId):  # noqa: N803
        self.deleted.append(eventId)
        return _Run({})


class _Service:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


@pytest.fixture
def events(db, employee, monkeypatch):
    account = GoogleAccount(employee_id=employee.id, google_email="ivanov@example.com")
    db.add(account)
    db.commit()
    db.add(Calendar(google_account_id=account.id, google_calendar_id="primary", calendar_name="Основной",
                    timezone="Europe/Moscow", is_primary=True))
    db.commit()
    recorder = RecordingEvents()
    monkeypatch.setattr(calendar_service, "_credentials_for", lambda _db, _account: object())
    monkeypatch.setattr(calendar_service.calendar_api, "build_service", lambda _creds: _Service(recorder))
    monkeypatch.setattr(calendar_service.calendar_api, "freebusy", lambda *a: [])
    return recorder


def _switch(db, enabled: bool) -> None:
    set_setting(db, GOOGLE_SYNC, {**get_setting(db, GOOGLE_SYNC), "enabled": enabled})


def _create(db, client, employee, service, workday, hh):
    booking, _google = booking_flow.create(
        db, client_id=client.id, employee_id=employee.id, service_id=service.id,
        start_at=datetime.combine(workday, time(hh), tzinfo=local_tz()), source=SOURCE_ADMIN, actor="test",
    )
    return booking


def test_по_умолчанию_google_включён(db):
    assert calendar_service.google_enabled(db) is True


def test_выключенный_google_не_пишет_и_откладывает_удаление(db, client, employee, service, workday, events, monkeypatch):
    first = _create(db, client, employee, service, workday, 10)
    assert first.google_event_id == "evt-1"

    _switch(db, False)
    second = _create(db, client, employee, service, workday, 12)
    assert second.google_event_id is None and events.inserted == [str(first.id)]

    booking_flow.cancel(db, first.id, actor="test")
    assert events.deleted == []
    assert [d["event_id"] for d in get_setting(db, GOOGLE_PENDING)["deletes"]] == ["evt-1"]

    def boom(*_args):
        raise AssertionError("Google спрошен при выключенном выключателе")

    monkeypatch.setattr(calendar_service.calendar_api, "freebusy", boom)
    monkeypatch.setattr(calendar_service, "sync_account_changes", boom)
    start = datetime.now(timezone.utc)
    assert calendar_service.get_busy_intervals(db, employee.id, start, start + timedelta(days=1)) == []
    assert calendar_service.sync_all_accounts(db) == 0


def test_включение_выгружает_накопленное(admin_client, db, client, employee, service, workday, events):
    moved = _create(db, client, employee, service, workday, 9)
    gone = _create(db, client, employee, service, workday, 10)
    _switch(db, False)
    booking_flow.reschedule(db, moved.id, new_start=datetime.combine(workday, time(15), tzinfo=local_tz()), actor="test")
    booking_flow.cancel(db, gone.id, actor="test")
    fresh = _create(db, client, employee, service, workday, 12)
    assert events.patched == [] and events.deleted == []

    response = admin_client.post(
        "/admin/settings/google", data={"enabled": "1", "back": "/admin/calendar?view=day"}, follow_redirects=False
    )
    location = response.headers["location"]
    assert location.startswith("/admin/calendar?view=day") and "ok=" in location
    assert calendar_service.google_enabled(db)
    assert events.deleted == [gone.google_event_id]
    assert events.patched == [moved.google_event_id]
    db.refresh(fresh)
    assert fresh.google_event_id and str(fresh.id) in events.inserted
    assert get_setting(db, GOOGLE_PENDING)["deletes"] == []


def test_событие_удалённое_в_google_заводится_заново(db, client, employee, service, workday, events):
    booking = _create(db, client, employee, service, workday, 10)
    events.missing.add(booking.google_event_id)
    result = calendar_service.reconcile(db)
    assert result == {"created": 1, "updated": 0, "deleted": 0, "failed": 0}
    assert db.get(Booking, booking.id).google_event_id == "evt-2"


def test_выключение_из_админки_и_настройки_записи_не_сбрасывают_выключатель(admin_client, db):
    response = admin_client.post("/admin/settings/google", data={"enabled": "0"}, follow_redirects=False)
    assert response.headers["location"].startswith("/admin/settings?ok=")
    assert not calendar_service.google_enabled(db)

    admin_client.post(
        "/admin/settings/booking",
        data={"slot_step": "30", "horizon_days": "90", "min_lead": "0", "sync_minutes": "15"},
        follow_redirects=False,
    )
    assert not calendar_service.google_enabled(db)
    assert get_setting(db, GOOGLE_SYNC)["interval_minutes"] == 15
    assert "Google Calendar выключен" in admin_client.get("/admin/calendar").text
