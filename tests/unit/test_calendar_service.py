"""Google Calendar: занятость, события, инкрементальная синхронизация.

Google не вызывается — вместо клиента googleapiclient подставляется заглушка.
"""

from datetime import datetime, time, timedelta, timezone

import pytest
from googleapiclient.errors import HttpError

from app.models.calendar import Calendar
from app.models.enums import BOOKED, CANCELLED, SOURCE_ADMIN
from app.models.google_account import GoogleAccount
from app.services import booking_service, calendar_service
from app.services.schedule_service import local_tz


# ==== Заглушки Google ====

class _Resp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "test"


def http_error(status: int) -> HttpError:
    return HttpError(_Resp(status), b"{}")


class _Executable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeEvents:
    """Повторяет ту часть events(), которой пользуется calendar_service."""

    def __init__(self, pages=None, insert_result=None):
        self.pages = list(pages or [])
        self.list_calls: list[dict] = []
        self.insert_result = insert_result or {"id": "evt-1"}
        self.patched: list[dict] = []
        self.deleted: list[str] = []

    def list(self, **params):
        self.list_calls.append(params)
        index = len(self.list_calls) - 1
        page = self.pages[index] if index < len(self.pages) else {}
        return _Executable(page)

    def insert(self, calendarId, body):  # noqa: N803 — имя параметра задано Google API
        self.inserted = {"calendarId": calendarId, "body": body}
        return _Executable(self.insert_result)

    def patch(self, calendarId, eventId, body):  # noqa: N803
        self.patched.append({"calendarId": calendarId, "eventId": eventId, "body": body})
        return _Executable({"id": eventId})

    def delete(self, calendarId, eventId):  # noqa: N803
        self.deleted.append(eventId)
        return _Executable({})


class FakeService:
    def __init__(self, events: FakeEvents):
        self._events = events

    def events(self):
        return self._events


@pytest.fixture
def google(db, employee, monkeypatch):
    """Подключённый Google-аккаунт с одним календарём и подменённым клиентом."""
    account = GoogleAccount(employee_id=employee.id, google_email="ivanov@example.com")
    db.add(account)
    db.commit()
    calendar = Calendar(
        google_account_id=account.id,
        google_calendar_id="primary",
        calendar_name="Основной",
        timezone="Europe/Moscow",
        is_primary=True,
    )
    db.add(calendar)
    db.commit()
    monkeypatch.setattr(calendar_service, "_credentials_for", lambda _db, _account: object())
    return account, calendar


def _use(monkeypatch, events: FakeEvents) -> FakeEvents:
    monkeypatch.setattr(
        calendar_service.calendar_api, "build_service", lambda _creds: FakeService(events)
    )
    return events


def _booking(db, client, employee, service, workday, hh=10):
    return booking_service.create_booking(
        db,
        client_id=client.id,
        employee_id=employee.id,
        service_id=service.id,
        start_at=datetime.combine(workday, time(hh, 0), tzinfo=local_tz()),
        source=SOURCE_ADMIN,
    )


# ==== Время ====

def test_наивное_время_считается_utc():
    naive = datetime(2026, 9, 10, 12, 0)
    assert calendar_service.as_utc(naive) == datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_время_с_поясом_переводится_в_utc():
    msk = datetime(2026, 9, 10, 15, 0, tzinfo=local_tz())
    assert calendar_service.as_utc(msk).tzinfo == timezone.utc


# ==== Выбор календаря ====

def test_календарь_по_умолчанию_это_primary(db, employee, google):
    _account, calendar = google
    assert calendar_service.default_calendar(db, employee.id).id == calendar.id


def test_явно_выбранный_календарь_важнее_primary(db, employee, google):
    account, primary = google
    other = Calendar(
        google_account_id=account.id, google_calendar_id="work@group", calendar_name="Рабочий"
    )
    db.add(other)
    db.commit()
    employee.default_calendar_id = other.id
    db.commit()
    assert calendar_service.default_calendar(db, employee.id).id == other.id
    assert primary.is_primary is True


# ==== Занятость ====

def test_freebusy_превращается_в_интервалы(db, employee, google, monkeypatch):
    _use(monkeypatch, FakeEvents())
    monkeypatch.setattr(
        calendar_service.calendar_api,
        "freebusy",
        lambda *a, **kw: [{"start": "2026-09-10T08:00:00Z", "end": "2026-09-10T09:00:00Z"}],
    )
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    busy = calendar_service.get_busy_intervals(db, employee.id, start, start + timedelta(days=1))
    assert busy == [
        (
            datetime(2026, 9, 10, 8, tzinfo=timezone.utc),
            datetime(2026, 9, 10, 9, tzinfo=timezone.utc),
        )
    ]


def test_занятость_только_по_рабочему_календарю(db, employee, google, monkeypatch):
    account, _primary = google
    work = Calendar(
        google_account_id=account.id, google_calendar_id="work@group", calendar_name="Рабочий"
    )
    db.add(work)
    db.commit()
    employee.default_calendar_id = work.id
    db.commit()
    asked: list[list[str]] = []
    _use(monkeypatch, FakeEvents())
    monkeypatch.setattr(
        calendar_service.calendar_api, "freebusy", lambda _service, ids, *_a: asked.append(ids) or []
    )
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    calendar_service.get_busy_intervals(db, employee.id, start, start + timedelta(days=1))
    assert asked == [["work@group"]]


def test_без_подключённого_аккаунта_занятость_пустая(db, employee):
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert calendar_service.get_busy_intervals(db, employee.id, start, start) == []


# ==== События ====

def test_push_booking_сохраняет_event_id(db, client, employee, service, workday, google, monkeypatch):
    events = _use(monkeypatch, FakeEvents(insert_result={"id": "evt-42"}))
    booking = _booking(db, client, employee, service, workday)
    assert calendar_service.push_booking(db, booking) == "evt-42"
    assert booking.google_event_id == "evt-42"
    assert booking.calendar_id == google[1].id
    assert events.inserted["body"]["extendedProperties"]["private"]["booking_id"] == str(booking.id)


def test_перенос_правит_то_же_событие(db, client, employee, service, workday, google, monkeypatch):
    events = _use(monkeypatch, FakeEvents())
    booking = _booking(db, client, employee, service, workday)
    calendar_service.push_booking(db, booking)
    assert calendar_service.push_reschedule(db, booking) is True
    assert events.patched[0]["eventId"] == "evt-1"


def test_удаление_несуществующего_события_не_ошибка(db, client, employee, service, workday, google, monkeypatch):
    class Gone(FakeEvents):
        def delete(self, calendarId, eventId):  # noqa: N803
            return _Executable(http_error(410))

    _use(monkeypatch, Gone())
    booking = _booking(db, client, employee, service, workday)
    booking.google_event_id = "evt-1"
    booking.calendar_id = google[1].id
    db.commit()
    assert calendar_service.push_cancel(db, booking) is True


# ==== Синхронизация Google → БД ====

def test_чужое_событие_не_трогает_брони(db):
    assert calendar_service._apply_event(db, {"id": "x", "status": "confirmed"}) == 0


def test_отмена_в_google_отменяет_бронь(db, client, employee, service, workday):
    booking = _booking(db, client, employee, service, workday)
    changed = calendar_service._apply_event(
        db,
        {
            "status": "cancelled",
            "extendedProperties": {"private": {"booking_id": str(booking.id)}},
        },
    )
    db.commit()
    assert changed == 1
    assert db.get(type(booking), booking.id).status == CANCELLED


def test_сдвиг_события_переносит_бронь(db, client, employee, service, workday):
    booking = _booking(db, client, employee, service, workday)
    new_start = booking.start_at + timedelta(hours=2)
    changed = calendar_service._apply_event(
        db,
        {
            "status": "confirmed",
            "start": {"dateTime": new_start.isoformat()},
            "end": {"dateTime": (new_start + timedelta(hours=1)).isoformat()},
            "extendedProperties": {"private": {"booking_id": str(booking.id)}},
        },
    )
    db.commit()
    assert changed == 1
    assert db.get(type(booking), booking.id).start_at == new_start
    assert db.get(type(booking), booking.id).status == BOOKED


def test_событие_без_изменений_не_считается_обновлением(db, client, employee, service, workday):
    booking = _booking(db, client, employee, service, workday)
    same = {
        "status": "confirmed",
        "start": {"dateTime": booking.start_at.isoformat()},
        "end": {"dateTime": booking.end_at.isoformat()},
        "extendedProperties": {"private": {"booking_id": str(booking.id)}},
    }
    assert calendar_service._apply_event(db, same) == 0


def test_синхронизация_проходит_все_страницы(db, client, employee, service, workday, google, monkeypatch):
    """Регрессия: без передачи pageToken цикл крутился вечно на второй странице."""
    _account, calendar = google
    first = _booking(db, client, employee, service, workday, hh=10)
    second = _booking(db, client, employee, service, workday, hh=12)
    events = FakeEvents(
        pages=[
            {
                "items": [
                    {
                        "status": "cancelled",
                        "extendedProperties": {"private": {"booking_id": str(first.id)}},
                    }
                ],
                "nextPageToken": "page-2",
            },
            {
                "items": [
                    {
                        "status": "cancelled",
                        "extendedProperties": {"private": {"booking_id": str(second.id)}},
                    }
                ],
                "nextSyncToken": "token-final",
            },
        ]
    )
    _use(monkeypatch, events)
    updated = calendar_service.sync_calendar_changes(db, FakeService(events), calendar)
    db.commit()

    assert updated == 2
    assert len(events.list_calls) == 2
    assert events.list_calls[1]["pageToken"] == "page-2"
    assert calendar.sync_token == "token-final"


def test_протухший_sync_token_приводит_к_полному_прогону(db, google, monkeypatch):
    _account, calendar = google
    calendar.sync_token = "старый"
    db.commit()
    events = FakeEvents(pages=[http_error(410), {"items": [], "nextSyncToken": "свежий"}])
    _use(monkeypatch, events)

    calendar_service.sync_calendar_changes(db, FakeService(events), calendar)

    assert events.list_calls[0]["syncToken"] == "старый"
    assert "syncToken" not in events.list_calls[1]
    assert "timeMin" in events.list_calls[1]  # полный прогон смотрит только будущее
    assert calendar.sync_token == "свежий"


def test_ошибка_google_не_роняет_синхронизацию(db, google, monkeypatch):
    _account, calendar = google
    events = FakeEvents(pages=[http_error(500)])
    _use(monkeypatch, events)
    assert calendar_service.sync_calendar_changes(db, FakeService(events), calendar) == 0
