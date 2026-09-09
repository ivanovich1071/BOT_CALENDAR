from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.config.security import hash_password
from app.db.database import get_db
from app.models.employee import Employee
from app.models.enums import ALL_PERMISSIONS, PERMISSION_LABELS_RU
from app.models.google_account import GoogleAccount
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/employees")


def _google_status(db: Session, employee_id: int) -> str:
    accounts = db.scalars(
        select(GoogleAccount).where(GoogleAccount.employee_id == employee_id)
    ).all()
    if not accounts:
        return "—"
    return ", ".join(a.google_email for a in accounts)


@router.get("")
async def list_employees(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("manage_employees") and not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    employees = db.scalars(
        select(Employee).options(selectinload(Employee.user)).order_by(Employee.name)
    ).all()
    data = [
        {
            "employee": e,
            "google_status": _google_status(db, e.id),
        }
        for e in employees
    ]
    return render(
        request,
        "employees/list.html",
        {"user": user, "nav": "employees", "items": data, "page_title": "Сотрудники"},
    )


@router.get("/new")
async def new_employee(
    request: Request,
    user: User = Depends(require_permission("manage_employees")),
):
    return render(
        request,
        "employees/form.html",
        {
            "user": user,
            "nav": "employees",
            "employee": None,
            "linked_user": None,
            "perms": ALL_PERMISSIONS,
            "perm_labels": PERMISSION_LABELS_RU,
            "selected_perms": [],
            "page_title": "Новый сотрудник",
        },
    )


@router.post("/create")
async def create_employee(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    specialization: str = Form(""),
    with_login: str = Form(""),
    login: str = Form(""),
    password: str = Form(""),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = Employee(
        name=name.strip(),
        phone=phone.strip() or None,
        specialization=specialization.strip() or None,
    )
    db.add(employee)
    db.flush()

    if with_login == "on" and login.strip() and password:
        if db.scalar(select(User).where(User.login == login.strip())):
            db.rollback()
            return redirect("/admin/employees/new", err="Логин уже занят")
        new_user = User(
            login=login.strip(),
            password_hash=hash_password(password),
            role="employee",
            permissions=list(permissions),
        )
        db.add(new_user)
        db.flush()
        employee.user_id = new_user.id

    db.commit()
    log_action(
        db,
        actor=user.login,
        action="employee.create",
        entity_type="employee",
        entity_id=employee.id,
        details={"name": employee.name, "login": login.strip() or None},
        user_id=user.id,
    )
    return redirect("/admin/employees", ok="Сотрудник добавлен")


@router.get("/{employee_id}/edit")
async def edit_employee(
    employee_id: int,
    request: Request,
    user: User = Depends(require_permission("manage_employees")),
    db: Session = Depends(get_db),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    linked_user = db.get(User, employee.user_id) if employee.user_id else None
    return render(
        request,
        "employees/form.html",
        {
            "user": user,
            "nav": "employees",
            "employee": employee,
            "linked_user": linked_user,
            "perms": ALL_PERMISSIONS,
            "perm_labels": PERMISSION_LABELS_RU,
            "selected_perms": (linked_user.permissions or []) if linked_user else [],
            "page_title": f"Сотрудник: {employee.name}",
        },
    )


@router.post("/{employee_id}/update")
async def update_employee(
    employee_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    specialization: str = Form(""),
    permissions: list[str] = Form([]),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/employees", err="Сотрудник не найден")
    employee.name = name.strip()
    employee.phone = phone.strip() or None
    employee.specialization = specialization.strip() or None
    employee.is_active = is_active == "on"
    if employee.user_id:
        linked = db.get(User, employee.user_id)
        if linked:
            linked.permissions = list(permissions)
            linked.is_active = is_active == "on"
    db.commit()
    log_action(
        db,
        actor=user.login,
        action="employee.update",
        entity_type="employee",
        entity_id=employee.id,
        user_id=user.id,
    )
    return redirect("/admin/employees", ok="Сохранено")


@router.post("/{employee_id}/reset-password")
async def reset_password(
    employee_id: int,
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_employees")),
):
    employee = db.get(Employee, employee_id)
    if employee is None or not employee.user_id:
        return redirect("/admin/employees", err="У сотрудника нет учётной записи")
    if len(password) < 8:
        return redirect(f"/admin/employees/{employee_id}/edit", err="Пароль минимум 8 символов")
    linked = db.get(User, employee.user_id)
    linked.password_hash = hash_password(password)
    db.commit()
    log_action(
        db,
        actor=user.login,
        action="employee.reset_password",
        entity_type="employee",
        entity_id=employee_id,
        user_id=user.id,
    )
    return redirect(f"/admin/employees/{employee_id}/edit", ok="Пароль обновлён")
