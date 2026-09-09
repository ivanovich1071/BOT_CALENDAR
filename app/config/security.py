"""Пароли (bcrypt), JWT-сессии, шифрование Google-токенов (Fernet)."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import get_settings


# ==== Пароли ====

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ==== JWT (cookie-сессии админки) ====

def create_access_token(subject: str, extra: dict | None = None, minutes: int = 12 * 60) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Бросает jwt.PyJWTError при невалидном/просроченном токене."""
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


# ==== Шифрование токенов Google перед записью в БД ====

def _fernet() -> Fernet:
    # Стабильный Fernet-ключ, производный от ENCRYPTION_KEY из .env
    settings = get_settings()
    digest = hashlib.sha256(settings.encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode()


def decrypt_secret(cipher: str) -> str:
    try:
        return _fernet().decrypt(cipher.encode("utf-8")).decode()
    except InvalidToken as exc:  # ключ ENCRYPTION_KEY сменился
        raise RuntimeError("Не удалось расшифровать токен: ENCRYPTION_KEY изменился?") from exc
