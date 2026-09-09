"""Общие зависимости FastAPI: текущий пользователь (cookie-JWT) и проверка прав."""

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config.security import decode_token
from app.db.database import get_db
from app.models.user import User

SESSION_COOKIE = "bp_session"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        _redirect_to_login()
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        _redirect_to_login()
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        _redirect_to_login()
    return user


def _redirect_to_login() -> None:
    raise HTTPException(status_code=303, headers={"Location": "/login"})


def require_permission(perm: str):
    """Фабрика зависимостей: пользователь обязан иметь право perm."""

    def dependency(
        user: User = Depends(get_current_user),
    ) -> User:
        if not user.has_permission(perm):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return dependency
