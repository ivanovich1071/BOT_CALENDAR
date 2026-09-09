"""Бизнес-слой Google Calendar: подключение аккаунтов, занятость, события, синк.

Правила:
- Токены в БД только шифрованными (Fernet).
- Источник истины по бизнесу — PostgreSQL; события — Google Calendar;
  связь — booking.google_event_id (+ extendedProperties.private.booking_id).
- Любая ошибка Google не ломает бронирование: свободный слот проверяется в БД,
  проблемы записи в Google фиксируются в audit-логе.
"""

import logging
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.security import decrypt_secret, encrypt_secret
from app.config.settings import get_settings
from app.integrations.google import calendar_api
from app.integrations.google import oauth as google_oauth
from app.models.booking import Booking
from app.models.calendar import Calendar
from app.models.employee import Employee
from app.models.enums import BOOKED, CANCELLED
from app.models.google_account import GoogleAccount

logger = logging.getLogger(__name__)

# Запас, с которым обновляем access-токен, не дожидаясь фактического истечения
TOKEN_REFRESH_MARGIN = timedelta(seconds=30)


class GoogleNotConfigured(Exception):
    pass


def _require_client() -> None:
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        raise GoogleNotConfigured("GOOGLE_CLIENT_ID/SECRET не заполнены (см. docs/GOOGLE_SETUP.md)")


def as_utc(dt: datetime | None) -> datetime | None:
    """Приводит время к tz-aware UTC (google-auth отдаёт expiry наивным)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _credentials_for(db: Session, account: GoogleAccount) -> Credentials | None:
    """Собирает google Credentials; обновляет токен при необходимости (обновление пишется в БД)."""
    refresh_token = decrypt_secret(account.refresh_token_enc) if account.refresh_token_enc else None
    access_token = decrypt_secret(account.access_token_enc) if account.access_token_enc else None
    if not (access_token or refresh_token):
        return None
    creds = calendar_api.make_credentials(access_token, refresh_token)

    expires_at = as_utc(account.token_expires_at)
    expiring = (
        expires_at is not None
        and expires_at <= datetime.now(timezone.utc) + TOKEN_REFRESH_MARGIN
    )
    if expiring or not creds.valid:
        if not creds.refresh_token:
            return None
        try:
            calendar_api.refresh(creds)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось обновить Google-токен аккаунта #%s", account.id)
            return None
        account.access_token_enc = encrypt_secret(creds.token or "")
        account.token_expires_at = as_utc(creds.expiry)
        db.commit()
    return creds


# ==== Подключение ====

def connect_account(db: Session, employee: Employee, code: str) -> GoogleAccount:
    """Обмен code → токены → создание GoogleAccount + загрузка списка календарей."""
    _require_client()
    tokens = google_oauth.exchange_code(code)
    email = google_oauth.fetch_user_email(tokens["access_token"])

    existing = db.scalar(
        select(GoogleAccount).where(
            GoogleAccount.employee_id == employee.id, GoogleAccount.google_email == email
        )
    )
    if existing:
        account = existing
    else:
        account = GoogleAccount(employee_id=employee.id, google_email=email)
        db.add(account)

    account.access_token_enc = encrypt_secret(tokens["access_token"])
    if tokens["refresh_token"]:  # Google может не прислать refresh при повторном consent
        account.refresh_token_enc = encrypt_secret(tokens["refresh_token"])
    account.token_expires_at = as_utc(tokens["expires_at"])
    db.commit()
    db.refresh(account)

    sync_calendars(db, account)
    return account


def sync_calendars(db: Session, account: GoogleAccount) -> int:
    """Загружает calendarList аккаунта в таблицу calendars. Возвращает число активных."""
    creds = _credentials_for(db, account)
    if creds is None:
        return 0
    service = calendar_api.build_service(creds)
    items = service.calendarList().list(showHidden=False).execute().get("items", [])
    seen = set()
    for item in items:
        cid = item["id"]
        if item.get("accessRole") not in ("owner", "writer"):
            continue
        seen.add(cid)
        row = db.scalar(
            select(Calendar).where(
                Calendar.google_account_id == account.id,
                Calendar.google_calendar_id == cid,
            )
        )
        if row is None:
            row = Calendar(google_account_id=account.id, google_calendar_id=cid)
            db.add(row)
        row.calendar_name = item.get("summary", cid)
        row.timezone = item.get("timeZone") or get_settings().timezone
        row.is_primary = bool(item.get("primary"))
        row.is_active = True
    # Календари, исчезнувшие из списка — деактивируем
    for row in db.scalars(
        select(Calendar).where(
            Calendar.google_account_id == account.id,
            Calendar.is_active.is_(True),
        )
    ):
        if row.google_calendar_id not in seen:
            row.is_active = False
    db.commit()
    return len(seen)


def default_calendar(db: Session, employee_id: int) -> Calendar | None:
    """Рабочий календарь сотрудника: явно выбранный → primary → первый активный."""
    employee = db.get(Employee, employee_id)
    if employee is not None and employee.default_calendar_id:
        chosen = db.get(Calendar, employee.default_calendar_id)
        if chosen is not None and chosen.is_active:
            return chosen
    for cal in employee_calendars(db, employee_id):
        if cal.is_primary:
            return cal
    cals = employee_calendars(db, employee_id)
    return cals[0] if cals else None


def employee_calendars(db: Session, employee_id: int) -> list[Calendar]:
    """Все активные календари сотрудника (по всем его Google-аккаунтам)."""
    accounts = db.scalars(
        select(GoogleAccount).where(GoogleAccount.employee_id == employee_id)
    ).all()
    result: list[Calendar] = []
    for acc in accounts:
        result.extend(
            db.scalars(
                select(Calendar).where(
                    Calendar.google_account_id == acc.id, Calendar.is_active.is_(True)
                )
            ).all()
        )
    return result


# ==== Занятость ====

def get_busy_intervals(
    db: Session, employee_id: int, start_utc: datetime, end_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """Занятые интервалы по всем календарям сотрудника. Ошибки Google → [] с логом."""
    busy: list[tuple[datetime, datetime]] = []
    accounts = db.scalars(
        select(GoogleAccount).where(GoogleAccount.employee_id == employee_id)
    ).all()
    if not accounts:
        return busy
    for account in accounts:
        cals = db.scalars(
            select(Calendar).where(
                Calendar.google_account_id == account.id, Calendar.is_active.is_(True)
            )
        ).all()
        if not cals:
            continue
        creds = _credentials_for(db, account)
        if creds is None:
            continue
        try:
            service = calendar_api.build_service(creds)
            slots = calendar_api.freebusy(
                service,
                [c.google_calendar_id for c in cals],
                start_utc.isoformat(),
                end_utc.isoformat(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("freebusy не удался для аккаунта #%s", account.id)
            continue
        for b in slots:
            s = as_utc(datetime.fromisoformat(b["start"].replace("Z", "+00:00")))
            e = as_utc(datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            busy.append((s, e))
    return busy


# ==== События записи ====

def _calendar_for(db: Session, booking: Booking) -> Calendar | None:
    """Календарь записи: сохранённый в брони, иначе рабочий календарь сотрудника."""
    if booking.calendar_id:
        cal = db.get(Calendar, booking.calendar_id)
        if cal is not None:
            return cal
    return default_calendar(db, booking.employee_id)


def _event_body(booking: Booking, tz: str) -> dict:
    client_name = booking.client.name or "Клиент"
    return {
        "summary": f"{booking.service.name} — {client_name}",
        "description": f"Запись #{booking.id}\nУслуга: {booking.service.name}\n"
        f"Клиент: {client_name}\nТелефон: {booking.client.phone or '—'}"
        + (f"\nЗаметка: {booking.notes}" if booking.notes else ""),
        "start": {"dateTime": booking.start_at.isoformat(), "timeZone": tz},
        "end": {"dateTime": booking.end_at.isoformat(), "timeZone": tz},
        "extendedProperties": {"private": {"booking_id": str(booking.id)}},
    }


def push_booking(db: Session, booking: Booking) -> str | None:
    """Создаёт событие в Google и возвращает google_event_id (или None, если нечего)."""
    calendar = _calendar_for(db, booking)
    if calendar is None:
        return None
    account = db.get(GoogleAccount, calendar.google_account_id)
    creds = _credentials_for(db, account)
    if creds is None:
        return None
    service = calendar_api.build_service(creds)
    event = (
        service.events()
        .insert(
            calendarId=calendar.google_calendar_id,
            body=_event_body(booking, calendar.timezone),
        )
        .execute()
    )
    booking.calendar_id = calendar.id
    booking.google_event_id = event["id"]
    db.commit()
    return event["id"]


def push_reschedule(db: Session, booking: Booking) -> bool:
    """Двигает существующее событие. Новое НЕ создаёт — дубля в календаре не будет."""
    if not booking.google_event_id:
        return False
    calendar = _calendar_for(db, booking)
    if calendar is None:
        return False
    account = db.get(GoogleAccount, calendar.google_account_id)
    creds = _credentials_for(db, account)
    if creds is None:
        return False
    service = calendar_api.build_service(creds)
    service.events().patch(
        calendarId=calendar.google_calendar_id,
        eventId=booking.google_event_id,
        body=_event_body(booking, calendar.timezone),
    ).execute()
    return True


def push_cancel(db: Session, booking: Booking) -> bool:
    if not booking.google_event_id:
        return False
    calendar = _calendar_for(db, booking)
    if calendar is None:
        return False
    account = db.get(GoogleAccount, calendar.google_account_id)
    creds = _credentials_for(db, account)
    if creds is None:
        return False
    service = calendar_api.build_service(creds)
    try:
        service.events().delete(
            calendarId=calendar.google_calendar_id, eventId=booking.google_event_id
        ).execute()
    except HttpError as e:
        if e.resp.status not in (404, 410):  # событие уже удалено — не ошибка
            raise
    return True


# ==== Синхронизация Google → БД (инкрементальная) ====

def _apply_event(db: Session, item: dict) -> int:
    """Переносит одно событие Google в бронь. Возвращает 1, если бронь изменилась."""
    private = (item.get("extendedProperties") or {}).get("private") or {}
    raw_id = private.get("booking_id")
    if not raw_id:
        return 0  # чужое событие календаря — нас не касается
    try:
        booking = db.get(Booking, int(raw_id))
    except (TypeError, ValueError):
        return 0
    if booking is None or booking.status != BOOKED:
        return 0

    if item.get("status") == "cancelled":
        booking.status = CANCELLED
        return 1

    start = (item.get("start") or {}).get("dateTime")
    end = (item.get("end") or {}).get("dateTime")
    if not (start and end):
        return 0  # событие стало «на весь день» — бронь не трогаем
    new_start = as_utc(datetime.fromisoformat(start.replace("Z", "+00:00")))
    new_end = as_utc(datetime.fromisoformat(end.replace("Z", "+00:00")))
    if new_start == as_utc(booking.start_at) and new_end == as_utc(booking.end_at):
        return 0
    booking.start_at, booking.end_at = new_start, new_end
    return 1


def sync_calendar_changes(db: Session, service, calendar: Calendar) -> int:
    """Инкрементальный проход по одному календарю. Возвращает число изменённых броней."""
    updated = 0
    sync_token = calendar.sync_token
    page_token: str | None = None
    retried_full = False

    while True:
        params: dict = {
            "calendarId": calendar.google_calendar_id,
            "maxResults": 250,
            "singleEvents": False,
        }
        if sync_token:
            params["syncToken"] = sync_token
        else:
            # Первый проход: только будущее, чтобы не вычитывать весь архив календаря
            params["timeMin"] = datetime.now(timezone.utc).isoformat()
        if page_token:
            params["pageToken"] = page_token

        try:
            result = service.events().list(**params).execute()
        except HttpError as e:
            if e.resp.status == 410 and not retried_full:
                # syncToken протух — один полный прогон и заново
                sync_token, page_token, retried_full = None, None, True
                continue
            logger.exception("sync не удался (календарь %s)", calendar.google_calendar_id)
            return updated

        for item in result.get("items", []):
            updated += _apply_event(db, item)

        page_token = result.get("nextPageToken")
        if page_token:
            continue  # nextSyncToken приходит только на последней странице
        new_token = result.get("nextSyncToken")
        if new_token:
            calendar.sync_token = new_token
        return updated


def sync_account_changes(db: Session, account: GoogleAccount) -> int:
    """Догоняет изменения, сделанные сотрудником вручную в Google Calendar.

    Возвращает число обновлённых броней.
    """
    creds = _credentials_for(db, account)
    if creds is None:
        return 0
    service = calendar_api.build_service(creds)
    updated = 0
    cals = db.scalars(
        select(Calendar).where(
            Calendar.google_account_id == account.id, Calendar.is_active.is_(True)
        )
    ).all()
    for cal in cals:
        updated += sync_calendar_changes(db, service, cal)
    db.commit()
    return updated


def sync_all_accounts(db: Session) -> int:
    """Прогон синхронизации по всем аккаунтам (вызывается планировщиком)."""
    total = 0
    for account in db.scalars(select(GoogleAccount)).all():
        try:
            total += sync_account_changes(db, account)
        except Exception:  # noqa: BLE001 — один сломанный аккаунт не останавливает остальные
            logger.exception("Синхронизация аккаунта #%s не удалась", account.id)
    return total
