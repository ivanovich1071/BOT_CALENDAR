"""OAuth callback Google: /oauth/google/callback (адрес должен совпадать
с GOOGLE_REDIRECT_URI в .env и Authorized redirect URI в Google Console)."""

import logging

import jwt
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from admin.flash import redirect
from app.config.security import decode_token
from app.db.database import get_db
from app.models.employee import Employee
from app.services import calendar_service
from app.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth")


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    if error:
        return redirect("/admin/calendars", err=f"Google вернул ошибку: {error}")
    if not code or not state:
        return redirect("/admin/calendars", err="Некорректный ответ Google")
    try:
        payload = decode_token(state)
        employee_id = int(payload.get("emp", 0))
    except (jwt.PyJWTError, ValueError):
        return redirect("/admin/calendars", err="Ссылка подключения устарела — попробуйте снова")
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/calendars", err="Сотрудник не найден")
    try:
        account = calendar_service.connect_account(db, employee, code)
    except calendar_service.GoogleNotConfigured:
        return redirect("/admin/calendars", err="Google OAuth не настроен")
    except Exception:  # noqa: BLE001
        logger.exception("OAuth exchange failed (employee #%s)", employee_id)
        return redirect("/admin/calendars", err="Не удалось подключить Google Calendar")

    log_action(
        db,
        actor=f"google:{account.google_email}",
        action="google.connect",
        entity_type="employee",
        entity_id=employee_id,
        details={"email": account.google_email},
    )
    return redirect(
        f"/admin/calendars?employee_id={employee_id}",
        ok=f"Google Calendar {account.google_email} подключён",
    )
