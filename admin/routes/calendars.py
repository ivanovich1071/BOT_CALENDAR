from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.config.security import create_access_token
from app.db.database import get_db
from app.integrations.google import oauth as google_oauth
from app.models.employee import Employee
from app.models.google_account import GoogleAccount
from app.services import calendar_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/calendars")


@router.get("")
async def calendars_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    employees = db.scalars(
        select(Employee).options(selectinload(Employee.google_accounts)).order_by(Employee.name)
    ).all()
    google_ready = True
    try:
        calendar_service._require_client()
    except calendar_service.GoogleNotConfigured:
        google_ready = False
    items = []
    for e in employees:
        accounts = [
            {
                "account": a,
                "calendars": [c for c in a.calendars if c.is_active],
            }
            for a in e.google_accounts
        ]
        active = calendar_service.default_calendar(db, e.id)
        items.append(
            {
                "employee": e,
                "accounts": accounts,
                # Плоский список для выбора рабочего календаря
                "all_calendars": calendar_service.employee_calendars(db, e.id),
                "active_calendar": active,
            }
        )
    return render(
        request,
        "calendars/list.html",
        {
            "user": user,
            "nav": "calendars",
            "items": items,
            "google_ready": google_ready,
            "can_manage": user.has_permission("manage_employees"),
            "page_title": "Google Календари",
        },
    )


@router.get("/connect/{employee_id}")
async def connect_google(
    employee_id: int,
    user=Depends(require_permission("manage_employees")),
    db: Session = Depends(get_db),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/calendars", err="Сотрудник не найден")
    try:
        calendar_service._require_client()
    except calendar_service.GoogleNotConfigured:
        return redirect(
            "/admin/calendars",
            err="Google OAuth не настроен — заполните GOOGLE_CLIENT_ID/SECRET (docs/GOOGLE_SETUP.md)",
        )
    state = create_access_token("oauth", extra={"emp": employee_id}, minutes=10)
    url = google_oauth.authorization_url(state)
    return redirect(url)


@router.post("/employee/{employee_id}/default")
async def set_default_calendar(
    employee_id: int,
    calendar_id: int = Form(0),
    user=Depends(require_permission("manage_employees")),
    db: Session = Depends(get_db),
):
    """Какой календарь считать рабочим: туда пишутся события записей."""
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/calendars", err="Сотрудник не найден")
    allowed = {c.id for c in calendar_service.employee_calendars(db, employee_id)}
    if calendar_id and calendar_id not in allowed:
        return redirect("/admin/calendars", err="Этот календарь не принадлежит сотруднику")
    employee.default_calendar_id = calendar_id or None
    db.commit()
    log_action(
        db, actor=user.login, action="google.set_default_calendar", entity_type="employee",
        entity_id=employee_id, details={"calendar_id": calendar_id or None}, user_id=user.id,
    )
    return redirect("/admin/calendars", ok=f"Рабочий календарь сотрудника {employee.name} обновлён")


@router.post("/{account_id}/disconnect")
async def disconnect(
    account_id: int,
    user=Depends(require_permission("manage_employees")),
    db: Session = Depends(get_db),
):
    account = db.get(GoogleAccount, account_id)
    if account:
        email = account.google_email
        db.delete(account)  # calendars удалятся каскадом
        db.commit()
        log_action(
            db, actor=user.login, action="google.disconnect", entity_type="google_account",
            entity_id=account_id, details={"email": email}, user_id=user.id,
        )
        return redirect("/admin/calendars", ok=f"Аккаунт {email} отключён")
    return redirect("/admin/calendars", err="Аккаунт не найден")


@router.post("/{account_id}/refresh")
async def refresh_calendars(
    account_id: int,
    user=Depends(require_permission("manage_employees")),
    db: Session = Depends(get_db),
):
    account = db.get(GoogleAccount, account_id)
    if account is None:
        return redirect("/admin/calendars", err="Аккаунт не найден")
    try:
        count = calendar_service.sync_calendars(db, account)
    except Exception:  # noqa: BLE001
        return redirect("/admin/calendars", err="Не удалось обновить список календарей")
    return redirect("/admin/calendars", ok=f"Календарей: {count}")
