"""Ссылка авторизации Google."""

from urllib.parse import parse_qs, urlparse

from app.config.settings import get_settings
from app.integrations.google import oauth


def _params(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_ссылка_без_pkce():
    """Регрессия: code_challenge ломал обмен кода.

    Ссылку и обмен выполняют разные запросы, каждый со своим Flow, поэтому
    code_verifier до fetch_token не доживает и Google отвечает invalid_grant.
    """
    params = _params(oauth.authorization_url("state-123"))
    assert "code_challenge" not in params
    assert "code_challenge_method" not in params


def test_запрашивается_refresh_token():
    """Без offline+consent Google не выдаст refresh_token, и доступ протухнет через час."""
    params = _params(oauth.authorization_url("state-123"))
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"


def test_состояние_и_адрес_возврата_на_месте():
    params = _params(oauth.authorization_url("state-123"))
    assert params["state"] == "state-123"
    assert params["redirect_uri"] == get_settings().google_redirect_uri


def test_запрашиваются_нужные_разрешения():
    scope = _params(oauth.authorization_url("state-123"))["scope"]
    assert "https://www.googleapis.com/auth/calendar.events" in scope
    assert "https://www.googleapis.com/auth/userinfo.email" in scope
