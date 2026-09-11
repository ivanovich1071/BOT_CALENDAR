"""Сотрудники: карточка, услуги, логин и роль, архив и удаление."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from admin.diff import changed_fields
from admin.flash import redirect
from admin.scope import own_employee_id
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.bot.profile import bot_username
from app.config.security import hash_password
from app.config.settings import get_settings
from app.services import staff_notify
from app.db.database import get_db
from app.models.booking import Booking
from app.models.employee import Employee
from app.models.enums import ADMIN, ALL_PERMISSIONS, EMPLOYEE, PERMISSION_LABELS_RU, ROLE_LABELS_RU, ROLES
from app.models.google_account import GoogleAccount
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.service import Service
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/employees")

LOGIN_RE = re.compile(r"[A-Za-z0-9_.@-]{3,64}")
MIN_PASSWORD = 8


def _google_status(db: Session, employee_id: int) -> str:
    accounts = db.scalars(
        select(GoogleAccount).where(GoogleAccount.employee_id == employee_id)
    ).all()
    if not accounts:
        return "—"
    return ", ".join(a.google_email for a in accounts)


def _snapshot(e: Employee) -> dict:
    return {
        "name": e.name,
        "phone": e.phone,
        "specialization": e.specialization,
        "bio": e.bio,
        "is_active": e.is_active,
        "services": sorted(s.id for s in e.services),
    }


def _text(form, key: str) -> str | None:
    return str(form.get(key) or "").strip() or None


def _selected_services(db: Session, form) -> list[Service]:
    ids = {int(v) for v in form.getlist("services") if str(v).isdigit()}
    return list(db.scalars(select(Service).where(Service.id.in_(ids)))) if ids else []


def _check_new_login(db: Session, login: str, password: str) -> str | None:
    if not LOGIN_RE.fullmatch(login):
        return "Логин: 3–64 символа — латиница, цифры, _ . @ -"
    if len(password) < MIN_PASSWORD:
        return f"Пароль — не короче {MIN_PASSWORD} символов"
    if db.scalar(select(User.id).where(User.login == login)):
        return "Логин уже занят"
    return None


def _permissions(form) -> list[str]:
    return sorted({p for p in form.getlist("permissions") if p in ALL_PERMISSIONS})


@router.get("")
async def list_employees(
    request: Request,
    archived: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("manage_employees") and not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    query = (
        select(Employee)
        .options(selectinload(Employee.user), selectinload(Employee.services))
        .where(Employee.archived_at.is_not(None) if archived else Employee.archived_at.is_(None))
        .order_by(Employee.name)
    )
    own = own_employee_id(db, user)
    if own is not None:
        query = query.where(Employee.id == own)
    employees = db.scalars(query).all()
    data = [
        {
            "employee": e,
            "google_status": _google_status(db, e.id),
            "services": ", ".join(s.name for s in e.services if s.archived_at is None) or "все услуги",
        }
        for e in employees
    ]
    return render(
        request,
        "employees/list.html",
        {"user": user, "nav": "employees", "items": data, "archived": archived, "page_title": "Сотрудники"},
    )


def _form(request: Request, db: Session, user: User, employee: Employee | None):
    services = db.scalars(
        select(Service).where(Service.archived_at.is_(None)).order_by(Service.sort_order, Service.name)
    ).all()
    linked_user = db.get(User, employee.user_id) if employee and employee.user_id else None
    bookings = db.scalar(select(func.count(Booking.id)).where(Booking.employee_id == employee.id)) if employee else 0
    return render(
        request,
        "employees/form.html",
        {
            "user": user,
            "nav": "employees",
            "employee": employee,
            "linked_user": linked_user,
            "services": services,
            "selected_services": {s.id for s in employee.services} if employee else set(),
            "perms": ALL_PERMISSIONS,
            "perm_labels": PERMISSION_LABELS_RU,
            "selected_perms": (linked_user.permissions or []) if linked_user else [],
            "roles": ROLES,
            "role_labels": ROLE_LABELS_RU,
            "bookings_count": bookings or 0,
            "page_title": f"Сотрудник: {employee.name}" if employee else "Новый сотрудник",
        },
    )


@router.get("/new")
async def new_employee(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    return _form(request, db, user, None)


@router.get("/{employee_id}/edit")
async def edit_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    return _form(request, db, user, employee)


@router.post("/create")
async def create_employee(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    form = await request.form()
    name = _text(form, "name")
    if not name:
        return redirect("/admin/employees/new", err="Укажите имя")
    login = _text(form, "login") if form.get("with_login") == "on" else None
    password = str(form.get("password") or "")
    role = str(form.get("role") or EMPLOYEE)
    if login:
        error = _check_new_login(db, login, password)
        if error:
            return redirect("/admin/employees/new", err=error)

    employee = Employee(
        name=name,
        phone=_text(form, "phone"),
        specialization=_text(form, "specialization"),
        bio=_text(form, "bio"),
    )
    employee.services = _selected_services(db, form)
    db.add(employee)
    db.flush()
    if login:
        account = User(
            login=login,
            password_hash=hash_password(password),
            role=role if role in ROLES else EMPLOYEE,
            permissions=_permissions(form),
        )
        db.add(account)
        db.flush()
        employee.user_id = account.id
    db.commit()
    log_action(
        db, actor=user.login, action="employee.create", entity_type="employee", entity_id=employee.id,
        details={"name": employee.name, "login": login}, user_id=user.id,
    )
    return redirect("/admin/employees", ok="Сотрудник добавлен")


@router.post("/{employee_id}/update")
async def update_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    form = await request.form()
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    back = f"/admin/employees/{employee_id}/edit"
    name = _text(form, "name")
    if not name:
        return redirect(back, err="Укажите имя")
    is_active = form.get("is_active") == "on"
    linked = db.get(User, employee.user_id) if employee.user_id else None

    # Сначала все проверки — изменения в сессии не должны остаться после отказа
    role = str(form.get("role") or (linked.role if linked else EMPLOYEE))
    new_login = _text(form, "login") if not linked and form.get("with_login") == "on" else None
    if linked:
        if role not in ROLES:
            return redirect(back, err="Неизвестная роль")
        if linked.id == user.id and (role != linked.role or not is_active):
            return redirect(back, err="Свою роль и доступ может изменить только другой администратор")
        other_admins = db.scalar(
            select(func.count(User.id)).where(User.role == ADMIN, User.is_active.is_(True), User.id != linked.id)
        ) or 0
        if linked.role == ADMIN and linked.is_active and (role != ADMIN or not is_active) and not other_admins:
            return redirect(back, err="Нельзя разжаловать или отключить последнего администратора")
    elif new_login:
        error = _check_new_login(db, new_login, str(form.get("password") or ""))
        if error:
            return redirect(back, err=error)

    before = _snapshot(employee)
    employee.name = name
    employee.phone = _text(form, "phone")
    employee.specialization = _text(form, "specialization")
    employee.bio = _text(form, "bio")
    employee.is_active = is_active
    employee.services = _selected_services(db, form)
    details = {}
    if linked:
        details = changed_fields(
            {"role": linked.role, "permissions": sorted(linked.permissions or [])},
            {"role": role, "permissions": _permissions(form)},
        )
        linked.role = role
        linked.permissions = _permissions(form)
        linked.is_active = is_active
    elif new_login:
        account = User(
            login=new_login,
            password_hash=hash_password(str(form.get("password"))),
            role=role if role in ROLES else EMPLOYEE,
            permissions=_permissions(form),
            is_active=is_active,
        )
        db.add(account)
        db.flush()
        employee.user_id = account.id
        details["login"] = {"было": None, "стало": new_login}
    db.commit()
    log_action(
        db, actor=user.login, action="employee.update", entity_type="employee", entity_id=employee.id,
        details={**changed_fields(before, _snapshot(employee)), **details}, user_id=user.id,
    )
    return redirect("/admin/employees", ok="Сохранено")


@router.post("/{employee_id}/reset-password")
async def reset_password(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    form = await request.form()
    password = str(form.get("password") or "")
    employee = db.get(Employee, employee_id)
    if employee is None or not employee.user_id:
        return redirect("/admin/employees", err="У сотрудника нет учётной записи")
    if len(password) < MIN_PASSWORD:
        return redirect(f"/admin/employees/{employee_id}/edit", err=f"Пароль минимум {MIN_PASSWORD} символов")
    linked = db.get(User, employee.user_id)
    linked.password_hash = hash_password(password)
    db.commit()
    log_action(
        db, actor=user.login, action="employee.reset_password", entity_type="employee",
        entity_id=employee_id, user_id=user.id,
    )
    return redirect(f"/admin/employees/{employee_id}/edit", ok="Пароль обновлён")


@router.post("/{employee_id}/telegram-link")
async def telegram_link(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    token = get_settings().bot_token
    if not token:
        return redirect(f"/admin/employees/{employee_id}/edit", err="BOT_TOKEN не заполнен в .env")
    try:
        username = await bot_username(token)
    except Exception:  # noqa: BLE001 — сеть до Telegram бывает недоступна
        return redirect(f"/admin/employees/{employee_id}/edit", err="Не удалось связаться с Telegram — попробуйте ещё раз")
    link_token = staff_notify.create_link_token(db, employee_id)
    log_action(
        db, actor=user.login, action="employee.telegram_link_created", entity_type="employee",
        entity_id=employee_id, user_id=user.id,
    )
    return render(
        request,
        "employees/telegram_link.html",
        {
            "user": user,
            "nav": "employees",
            "employee": employee,
            "link": f"https://t.me/{username}?start={staff_notify.PAYLOAD_PREFIX}{link_token}",
            "hours": int(staff_notify.LINK_TTL.total_seconds() // 3600),
            "page_title": f"Уведомления: {employee.name}",
        },
    )


@router.post("/{employee_id}/telegram-unlink")
async def telegram_unlink(
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    employee.telegram_user_id = None
    db.commit()
    log_action(
        db, actor=user.login, action="employee.telegram_unlink", entity_type="employee",
        entity_id=employee_id, user_id=user.id,
    )
    return redirect(f"/admin/employees/{employee_id}/edit", ok="Telegram отвязан — уведомления больше не приходят")


@router.post("/{employee_id}/archive")
async def archive_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    if employee.user_id == user.id:
        return redirect(f"/admin/employees/{employee_id}/edit", err="Себя в архив перенести нельзя")
    employee.archived_at = datetime.now(timezone.utc)
    # Ушедший сотрудник не должен входить в админку
    if employee.user_id:
        db.get(User, employee.user_id).is_active = False
    db.commit()
    log_action(db, actor=user.login, action="employee.archive", entity_type="employee", entity_id=employee_id, user_id=user.id)
    return redirect("/admin/employees", ok=f"{employee.name} — в архиве: скрыт из бота, вход в админку отключён")


@router.post("/{employee_id}/restore")
async def restore_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    employee.archived_at = None
    db.commit()
    log_action(db, actor=user.login, action="employee.restore", entity_type="employee", entity_id=employee_id, user_id=user.id)
    return redirect(f"/admin/employees/{employee_id}/edit", ok="Сотрудник восстановлен — проверьте «Активен» и доступ в админку")


@router.post("/{employee_id}/delete")
async def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    bookings = db.scalar(select(func.count(Booking.id)).where(Booking.employee_id == employee_id)) or 0
    if bookings:
        return redirect(f"/admin/employees/{employee_id}/edit", err=f"У сотрудника есть записи ({bookings}) — перенесите его в архив")
    if employee.user_id == user.id:
        return redirect(f"/admin/employees/{employee_id}/edit", err="Себя удалить нельзя")
    name = employee.name
    # Массовые DELETE: связи ORM у сотрудника без каскадов, база удалит календари и связи с услугами сама
    db.execute(delete(ScheduleException).where(ScheduleException.employee_id == employee_id))
    db.execute(delete(Schedule).where(Schedule.employee_id == employee_id))
    db.execute(delete(GoogleAccount).where(GoogleAccount.employee_id == employee_id))
    db.execute(update(Employee).where(Employee.id == employee_id).values(default_calendar_id=None))
    db.execute(delete(Employee).where(Employee.id == employee_id))
    db.commit()
    db.expire_all()
    log_action(db, actor=user.login, action="employee.delete", entity_type="employee", entity_id=employee_id, details={"name": name}, user_id=user.id)
    return redirect("/admin/employees", ok=f"Сотрудник «{name}» удалён")
