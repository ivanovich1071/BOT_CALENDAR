"""Тонкая обёртка над Google Calendar API v3."""

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config.settings import get_settings

TOKEN_URI = "https://oauth2.googleapis.com/token"


def make_credentials(access_token: str | None, refresh_token: str | None) -> Credentials:
    s = get_settings()
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=[
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    )


def refresh(creds: Credentials) -> Credentials:
    creds.refresh(GoogleAuthRequest())
    return creds


def build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def freebusy(service, calendar_ids: list[str], time_min: str, time_max: str) -> list[dict]:
    """Занятые интервалы по списку календарей: [{"start","end"}, ...]"""
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": cid} for cid in calendar_ids],
    }
    result = service.freebusy().query(body=body).execute()
    busy: list[dict] = []
    for cal in result.get("calendars", {}).values():
        busy.extend(cal.get("busy", []))
    return busy


__all__ = [
    "HttpError",
    "make_credentials",
    "refresh",
    "build_service",
    "freebusy",
    "TOKEN_URI",
]
