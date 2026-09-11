from datetime import date, time, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.flash import redirect
from admin.scope import FOREIGN, can_touch, own_employee_id
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.employee import Employee
from app.models.enums import EXC_BLOCK, EXC_DAY_OFF, EXC_EXTRA, EXCEPTION_KIND_LABELS_RU, EXCEPTION_KINDS
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.services.audit_service import log_action
from app.services.schedule_service import WEEKDAY_RU, local_now

router = APIRouter(prefix="/admin/schedule")

# Исключение на срок больше года — почти наверняка опечатка в дате
MAX_EXCEPTION_DAYS = 366
# Прошедшие исключения показываем за последний месяц
PAST_EXCEPTIONS_DAYS = 30


def _parse_time(v: str | None) -> time | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        hh, mm = v.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def _parse_date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(str(v or "").strip())
    except ValueError:
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
    q = (
        select(Employee)
        .where(Employee.is_active.is_(True), Employee.archived_at.is_(None))
        .order_by(Employee.name)
    )
    own = own_employee_id(db, user)
    if own is not None:
        q = q.where(Employee.id == own)
    employees = db.scalars(q).all()
    chosen = None
    rows: dict[int, Schedule] = {}
    exceptions: list[ScheduleException] = []
    today = local_now().date()
    if employees:
        chosen = next((e for e in employees if e.id == employee_id), employees[0])
        for s in db.scalars(select(Schedule).where(Schedule.employee_id == chosen.id)):
            rows[s.weekday] = s
        exceptions = db.scalars(
            select(ScheduleException)
            .where(
                ScheduleException.employee_id == chosen.id,
                ScheduleException.date_to >= today - timedelta(days=PAST_EXCEPTIONS_DAYS),
            )
            .order_by(ScheduleException.date_from)
        ).all()
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
            "exceptions": exceptions,
            "kinds": EXCEPTION_KINDS,
            "kind_labels": EXCEPTION_KIND_LABELS_RU,
            "today": today,
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
    if not can_touch(db, user, employee_id):
        return redirect("/admin/schedule", err=FOREIGN)

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


@router.post("/exceptions/add")
async def add_exception(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_schedule")),
):
    form = await request.form()
    employee_id = int(form.get("employee_id") or 0)
    back = f"/admin/schedule?employee_id={employee_id}"
    if db.get(Employee, employee_id) is None:
        return redirect("/admin/schedule", err="Сотрудник не найден")
    if not can_touch(db, user, employee_id):
        return redirect("/admin/schedule", err=FOREIGN)

    kind = str(form.get("kind") or "")
    if kind not in EXCEPTION_KINDS:
        return redirect(back, err="Выберите тип исключения")
    date_from = _parse_date(form.get("date_from"))
    date_to = _parse_date(form.get("date_to")) or date_from
    if date_from is None:
        return redirect(back, err="Укажите дату")
    if date_to < date_from:
        return redirect(back, err="Дата окончания раньше даты начала")
    if (date_to - date_from).days >= MAX_EXCEPTION_DAYS:
        return redirect(back, err="Исключение — не длиннее года")

    start = _parse_time(form.get("start_time"))
    end = _parse_time(form.get("end_time"))
    if kind == EXC_DAY_OFF:
        start = end = None
    elif kind == EXC_EXTRA and not (start and end and end > start):
        return redirect(back, err="Для дополнительного окна укажите время: начало раньше конца")
    elif kind == EXC_BLOCK and (start or end) and not (start and end and end > start):
        return redirect(back, err="Закрытое время: укажите начало и конец или оставьте оба пустыми — закроется весь день")

    note = str(form.get("note") or "").strip()[:200] or None
    db.add(
        ScheduleException(
            employee_id=employee_id,
            date_from=date_from,
            date_to=date_to,
            kind=kind,
            start_time=start,
            end_time=end,
            note=note,
        )
    )
    db.commit()
    log_action(
        db, actor=user.login, action="schedule.exception_add", entity_type="employee", entity_id=employee_id,
        details={
            "kind": kind,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "start": start.strftime("%H:%M") if start else None,
            "end": end.strftime("%H:%M") if end else None,
        },
        user_id=user.id,
    )
    return redirect(back, ok=f"Добавлено: {EXCEPTION_KIND_LABELS_RU[kind].lower()}")


@router.post("/exceptions/{exception_id}/delete")
async def delete_exception(
    exception_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_schedule")),
):
    row = db.get(ScheduleException, exception_id)
    if row is None:
        return redirect("/admin/schedule", err="Исключение не найдено")
    employee_id = row.employee_id
    if not can_touch(db, user, employee_id):
        return redirect("/admin/schedule", err=FOREIGN)
    details = {"kind": row.kind, "date_from": row.date_from.isoformat(), "date_to": row.date_to.isoformat()}
    db.delete(row)
    db.commit()
    log_action(
        db, actor=user.login, action="schedule.exception_delete", entity_type="employee",
        entity_id=employee_id, details=details, user_id=user.id,
    )
    return redirect(f"/admin/schedule?employee_id={employee_id}", ok="Исключение удалено")
