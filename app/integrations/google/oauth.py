"""OAuth 2.0 для Google Calendar (уровень интеграции, без БД)."""

from google_auth_oauthlib.flow import Flow

from app.config.settings import get_settings

# calendar.events — создание/чтение/правка событий календарей;
# calendar.readonly — freebusy-проверки; userinfo.email — определить email аккаунта
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _client_config() -> dict:
    s = get_settings()
    return {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [s.google_redirect_uri],
        }
    }


def make_flow() -> Flow:
    s = get_settings()
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=s.google_redirect_uri
    )


def authorization_url(state: str) -> str:
    flow = make_flow()
    url, _ = flow.authorization_url(
        access_type="offline",          # нужен refresh_token
        prompt="consent",               # гарантировать выдачу refresh_token
        include_granted_scopes=False,
        state=state,
    )
    return url


def exchange_code(code: str) -> dict:
    """Меняет authorization_code на токены. Возвращает access/refresh/expiry."""
    flow = make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expiry,  # tz-aware datetime
    }


def fetch_user_email(access_token: str) -> str:
    import httpx

    resp = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("email", "")
