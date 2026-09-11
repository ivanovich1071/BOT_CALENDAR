"""Пользователи админки: логины, роли, права, привязка к сотрудникам."""

import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from admin.diff import changed_fields
from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import require_permission
from app.config.security import hash_password
from app.db.database import get_db
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.enums import (
    ADMIN,
    ALL_PERMISSIONS,
    EMPLOYEE,
    PERMISSION_LABELS_RU,
    ROLE_DEFAULT_PERMISSIONS,
    ROLE_LABELS_RU,
    ROLES,
)
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/users")

LOGIN_RE = re.compile(r"[A-Za-z0-9_.@-]{3,64}")
MIN_PASSWORD = 8


def _other_active_admins(db: Session, exclude_id: int) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.role == ADMIN, User.is_active.is_(True), User.id != exclude_id)
    ) or 0


def _snapshot(u: User) -> dict:
    return {
        "login": u.login,
        "role": u.role,
        "permissions": sorted(u.permissions or []),
        "is_active": u.is_active,
    }


def _form(request: Request, db: Session, user: User, target: User | None):
    employees = db.scalars(
        select(Employee).where(Employee.archived_at.is_(None)).order_by(Employee.name)
    ).all()
    linked = db.scalar(select(Employee.id).where(Employee.user_id == target.id)) if target else None
    return render(
        request,
        "users/form.html",
        {
            "user": user,
            "nav": "users",
            "target": target,
            "linked_employee_id": linked,
            "employees": employees,
            "roles": ROLES,
            "role_labels": ROLE_LABELS_RU,
            "role_defaults": {
                r: [PERMISSION_LABELS_RU[p] for p in perms] for r, perms in ROLE_DEFAULT_PERMISSIONS.items()
            },
            "perms": ALL_PERMISSIONS,
            "perm_labels": PERMISSION_LABELS_RU,
            "page_title": f"Пользователь {target.login}" if target else "Новый пользователь",
        },
    )


@router.get("")
async def list_users(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_employees")),
):
    items = db.scalars(select(User).options(selectinload(User.employee)).order_by(User.login)).all()
    return render(
        request,
        "users/list.html",
        {"user": user, "nav": "users", "items": items, "role_labels": ROLE_LABELS_RU, "page_title": "Пользователи"},
    )


@router.get("/new")
async def new_user(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_employees")),
):
    return _form(request, db, user, None)


@router.get("/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_employees")),
):
    target = db.get(User, user_id)
    if target is None:
        return redirect("/admin/users", err="Пользователь не найден")
    return _form(request, db, user, target)


@router.post("/save")
async def save_user(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    form = await request.form()
    user_id = int(form.get("user_id") or 0)
    login = str(form.get("login") or "").strip()
    role = str(form.get("role") or EMPLOYEE)
    password = str(form.get("password") or "")
    employee_id = int(form.get("employee_id") or 0)
    is_active = form.get("is_active") == "on"
    permissions = sorted({p for p in form.getlist("permissions") if p in ALL_PERMISSIONS})
    back = f"/admin/users/{user_id}/edit" if user_id else "/admin/users/new"

    if not LOGIN_RE.fullmatch(login):
        return redirect(back, err="Логин: 3–64 символа — латиница, цифры, _ . @ -")
    if role not in ROLES:
        return redirect(back, err="Неизвестная роль")
    if db.scalar(select(User.id).where(User.login == login, User.id != user_id)):
        return redirect(back, err="Логин уже занят")
    if password and len(password) < MIN_PASSWORD:
        return redirect(back, err=f"Пароль — не короче {MIN_PASSWORD} символов")

    if user_id:
        target = db.get(User, user_id)
        if target is None:
            return redirect("/admin/users", err="Пользователь не найден")
        if target.id == user.id and (role != target.role or not is_active):
            return redirect(back, err="Свою роль и доступ может изменить только другой администратор")
        losing_admin = target.role == ADMIN and target.is_active and (role != ADMIN or not is_active)
        if losing_admin and _other_active_admins(db, target.id) == 0:
            return redirect(back, err="Нельзя разжаловать или отключить последнего администратора")
        before = _snapshot(target)
    else:
        if not password:
            return redirect(back, err="Задайте пароль для нового пользователя")
        target = User(login=login, password_hash=hash_password(password))
        db.add(target)
        before = {}

    target.login = login
    target.role = role
    target.permissions = permissions
    target.is_active = is_active
    if password and user_id:
        target.password_hash = hash_password(password)
    db.flush()

    current = db.scalar(select(Employee).where(Employee.user_id == target.id))
    if employee_id != (current.id if current else 0):
        if current:
            current.user_id = None
        if employee_id:
            employee = db.get(Employee, employee_id)
            if employee is None or (employee.user_id and employee.user_id != target.id):
                db.rollback()
                return redirect(back, err="У этого сотрудника уже есть логин")
            db.flush()
            employee.user_id = target.id
    db.commit()

    details = changed_fields(before, _snapshot(target))
    if password and user_id:
        details["password"] = "изменён"
    if employee_id != (current.id if current else 0):
        details["employee_id"] = {"было": current.id if current else None, "стало": employee_id or None}
    log_action(
        db, actor=user.login, action="user.update" if user_id else "user.create",
        entity_type="user", entity_id=target.id, details=details, user_id=user.id,
    )
    return redirect("/admin/users", ok="Пользователь сохранён")


@router.post("/{user_id}/delete")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    target = db.get(User, user_id)
    if target is None:
        return redirect("/admin/users", err="Пользователь не найден")
    if target.id == user.id:
        return redirect("/admin/users", err="Нельзя удалить себя")
    if target.role == ADMIN and target.is_active and _other_active_admins(db, target.id) == 0:
        return redirect("/admin/users", err="Нельзя удалить последнего администратора")
    login = target.login
    # Журнал остаётся: действия удалённого пользователя видны по логину в поле «кто»
    db.execute(update(AuditLog).where(AuditLog.user_id == target.id).values(user_id=None))
    db.execute(update(Employee).where(Employee.user_id == target.id).values(user_id=None))
    db.delete(target)
    db.commit()
    log_action(
        db, actor=user.login, action="user.delete", entity_type="user",
        entity_id=user_id, details={"login": login}, user_id=user.id,
    )
    return redirect("/admin/users", ok=f"Пользователь {login} удалён")
