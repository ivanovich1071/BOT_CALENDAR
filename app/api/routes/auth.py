"""Вход/выход админки. Сессия — JWT в HttpOnly cookie."""

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.templating import render
from app.api.dependencies import SESSION_COOKIE
from app.config.security import create_access_token, verify_password
from app.config.settings import get_settings
from app.db.database import get_db
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter()


@router.get("/login")
async def login_page(request: Request, error: str | None = None):
    if request.cookies.get(SESSION_COOKIE):
        return Response(status_code=303, headers={"Location": "/admin"})
    return render(request, "login.html", {"error": error})


@router.post("/login")
async def login(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.login == login.strip()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        log_action(db, actor=login.strip() or "?", action="auth.failed")
        return render(
            request,
            "login.html",
            {"error": "Неверный логин или пароль"},
            status_code=401,
        )

    token = create_access_token(str(user.id))
    log_action(db, actor=user.login, action="auth.login", user_id=user.id)
    resp = Response(status_code=303, headers={"Location": "/admin"})
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=get_settings().is_prod,
        max_age=12 * 60 * 60,
        path="/",
    )
    return resp


@router.post("/logout")
async def logout():
    resp = Response(status_code=303, headers={"Location": "/login"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
