"""Бизнес-слой Google Calendar: подключение аккаунтов, занятость, события, синк.

Правила:
- Токены в БД только шифрованными (Fernet).
- Источник истины по бизнесу — PostgreSQL; события — Google Calendar;
  связь — booking.google_event_id (+ extendedProperties.private.booking_id).
- Любая ошибка Google не ломает бронирование: свободный слот проверяется в БД,
  проблемы записи в Google фиксируются в audit-логе.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


class GoogleNotConfigured(Exception):
    pass


def _require_client() -> None:
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        raise GoogleNotConfigured("GOOGLE_CLIENT_ID/SECRET не заполнены (см. docs/GOOGLE_SETUP.md)")


def _credentials_for(db: Session, account: GoogleAccount) -> Credentials | None:
    """Собирает google Credentials; обновляет токен при необходимости (обновление пишется в БД)."""
    refresh_token = decrypt_secret(account.refresh_token_enc) if account.refresh_token_enc else None
    access_token = decrypt_secret(account.access_token_enc) if account.access_token_enc else None
    if not (access_token or refresh_token):
        return None
    creds = calendar_api.make_credentials(access_token, refresh_token)
    expired = account.token_expires_at is not None and account.token_expires_at <= datetime.now(
        account.token_expires_at.tzinfo or ZoneInfo("UTC")
    ) - timedelta(seconds=30)
    if (creds.valid is False) or expired:
        if not creds.refresh_token:
            return None
        try:
            calendar_api.refresh(creds)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось обновить Google-токен аккаунта #%s", account.id)
            return None
        account.access_token_enc = encrypt_secret(creds.token or "")
        account.token_expires_at = creds.expiry
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
    account.token_expires_at = tokens["expires_at"]
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
    """Основной календарь сотрудника (primary, иначе первый активный)."""
    accounts = db.scalars(
        select(GoogleAccount).where(GoogleAccount.employee_id == employee_id)
    ).all()
    for acc in accounts:
        cals = db.scalars(
            select(Calendar).where(
                Calendar.google_account_id == acc.id, Calendar.is_active.is_(True)
            )
        ).all()
        primary = next((c for c in cals if c.is_primary), None)
        if primary:
            return primary
        if cals:
            return cals[0]
    return None


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
            s = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
            busy.append((s, e))
    return busy


# ==== События записи ====

def _event_body(booking: Booking, tz: str) -> dict:
    return {
        "summary": f"{booking.service.name} — {booking.client.name or 'Клиент'}",
        "description": f"Запись #{booking.id}\nУслуга: {booking.service.name}\n"
        f"Клиент: {booking.client.name or '—'}\nТелефон: {booking.client.phone or '—'}"
        + (f"\nЗаметка: {booking.notes}" if booking.notes else ""),
        "start": {"dateTime": booking.start_at.isoformat(), "timeZone": tz},
        "end": {"dateTime": booking.end_at.isoformat(), "timeZone": tz},
        "extendedProperties": {"private": {"booking_id": str(booking.id)}},
    }


def push_booking(db: Session, booking: Booking) -> str | None:
    """Создаёт событие в Google и возвращает google_event_id (или None, если нечего)."""
    calendar = db.get(Calendar, booking.calendar_id) if booking.calendar_id else default_calendar(db, booking.employee_id)
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
    if not booking.google_event_id:
        return False
    calendar = db.get(Calendar, booking.calendar_id) if booking.calendar_id else default_calendar(db, booking.employee_id)
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
    calendar = db.get(Calendar, booking.calendar_id)
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

def sync_account_changes(db: Session, account: GoogleAccount) -> int:
    """Догоняет изменения, сделанные сотрудником вручную в Google Calendar.

    Возвращает число обновлённых броней.
    """
    creds = _credentials_for(db, account)
    if creds is None:
        return 0
    updated = 0
    cals = db.scalars(
        select(Calendar).where(
            Calendar.google_account_id == account.id, Calendar.is_active.is_(True)
        )
    ).all()
    for cal in cals:
        service = calendar_api.build_service(creds)
        sync_token = account.sync_token
        while True:
            try:
                result = (
                    service.events()
                    .list(
                        calendarId=cal.google_calendar_id,
                        syncToken=sync_token,
                        maxResults=250,
                        singleEvents=False,
                    )
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 410:  # syncToken устарел — полный прогон
                    sync_token = None
                    continue
                logger.exception("sync list не удался (calendar %s)", cal.google_calendar_id)
                break
            for item in result.get("items", []):
                bid = (item.get("extendedProperties", {}).get("private", {}) or {}).get("booking_id")
                if not bid:
                    continue
                booking = db.get(Booking, int(bid))
                if booking is None:
                    continue
                if item.get("status") == "cancelled" or item.get("deleted"):
                    if booking.status == BOOKED:
                        booking.status = CANCELLED
                        updated += 1
                else:
                    start = item.get("start", {}).get("dateTime")
                    end = item.get("end", {}).get("dateTime")
                    if start and end:
                        new_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        new_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                        if new_start != booking.start_at and booking.status == BOOKED:
                            booking.start_at, booking.end_at = new_start, new_end
                            updated += 1
            sync_token = result.get("nextSyncToken")
            if not result.get("nextPageToken"):
                break
        if sync_token:
            account.sync_token = sync_token
    db.commit()
    return updated
