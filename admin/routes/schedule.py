from datetime import time

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.employee import Employee
from app.models.schedule import Schedule
from app.services.audit_service import log_action
from app.services.schedule_service import WEEKDAY_RU

router = APIRouter(prefix="/admin/schedule")


def _parse_time(v: str | None) -> time | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        hh, mm = v.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


@router.get("")
async def schedule_page(
    request: Request,
    employee_id: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("manage_schedule") and not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
    ).all()
    chosen = None
    rows: dict[int, Schedule] = {}
    if employees:
        chosen = next((e for e in employees if e.id == employee_id), employees[0])
        for s in db.scalars(select(Schedule).where(Schedule.employee_id == chosen.id)):
            rows[s.weekday] = s
    return render(
        request,
        "schedule/edit.html",
        {
            "user": user,
            "nav": "schedule",
            "employees": employees,
            "chosen": chosen,
            "days": list(enumerate(WEEKDAY_RU)),
            "rows": rows,
            "can_manage": user.has_permission("manage_schedule"),
            "page_title": "Расписание",
        },
    )


@router.post("/save")
async def save_schedule(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_schedule")),
):
    form = await request.form()
    employee_id = int(form.get("employee_id") or 0)
    employee = db.get(Employee, employee_id)
    if employee is None:
        return redirect("/admin/schedule", err="Сотрудник не найден")

    saved = 0
    for wd in range(7):
        start = _parse_time(form.get(f"start_{wd}"))
        end = _parse_time(form.get(f"end_{wd}"))
        break_start = _parse_time(form.get(f"break_start_{wd}"))
        break_end = _parse_time(form.get(f"break_end_{wd}"))
        active = form.get(f"active_{wd}") == "on" and start is not None and end is not None and end > start
        if not active:
            continue
        row = db.scalar(
            select(Schedule).where(Schedule.employee_id == employee_id, Schedule.weekday == wd)
        )
        if row is None:
            row = Schedule(employee_id=employee_id, weekday=wd)
            db.add(row)
        row.start_time = start
        row.end_time = end
        row.break_start = break_start if break_start and break_end else None
        row.break_end = break_end if break_start and break_end else None
        row.is_active = True
        saved += 1
    # Дни, снятые с активности, деактивируем
    for wd in range(7):
        if form.get(f"active_{wd}") != "on":
            row = db.scalar(
                select(Schedule).where(Schedule.employee_id == employee_id, Schedule.weekday == wd)
            )
            if row:
                row.is_active = False
    db.commit()
    log_action(
        db, actor=user.login, action="schedule.save", entity_type="employee",
        entity_id=employee_id, details={"days": saved}, user_id=user.id,
    )
    return redirect(f"/admin/schedule?employee_id={employee_id}", ok="Расписание сохранено")
