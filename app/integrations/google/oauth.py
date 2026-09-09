"""OAuth 2.0 для Google Calendar (уровень интеграции, без БД)."""

import os
from datetime import timezone

from google_auth_oauthlib.flow import Flow

from app.config.settings import get_settings

# Google выдаёт больше разрешений, чем мы просим: к userinfo.email он сам
# добавляет openid и короткий email. oauthlib считает расхождение ошибкой и
# бросает Warning прямо из fetch_token, до того как мы получим токены.
# Дописать openid в SCOPES не спасает — короткий email всё равно не совпадёт.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

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
        _client_config(),
        scopes=SCOPES,
        redirect_uri=s.google_redirect_uri,
        # PKCE выключен намеренно: ссылку авторизации и обмен кода выполняют
        # разные HTTP-запросы, каждый со своим Flow, и code_verifier между ними
        # не переживает — Google ответил бы invalid_grant. Клиент
        # конфиденциальный (есть client_secret), для него PKCE не обязателен.
        autogenerate_code_verifier=False,
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
    """Меняет authorization_code на токены. Возвращает access/refresh/expiry (UTC)."""
    flow = make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    expiry = creds.expiry
    if expiry is not None and expiry.tzinfo is None:
        # google-auth отдаёт expiry наивным, но это UTC — помечаем явно,
        # иначе в timestamptz уедет часовой пояс сервера
        expiry = expiry.replace(tzinfo=timezone.utc)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expires_at": expiry,
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
