"""Календарь в админке: день всех сотрудников, неделя одного, неделя всех, месяц."""

from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from admin.scope import own_employee_id, visible_employees
from admin.templating import render
from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.models.enums import BOOKING_STATUS_LABELS_RU
from app.services import calendar_grid, calendar_service
from app.services.schedule_service import local_now

router = APIRouter(prefix="/admin/calendar")

VIEWS = {"day": "День", "week": "Неделя", "week_all": "Неделя всех", "month": "Месяц"}


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _shift(view: str, day: date, step: int) -> date:
    """Дата соседнего периода: день, неделя или месяц назад/вперёд."""
    if view == "day":
        return day + timedelta(days=step)
    if view == "month":
        first = day.replace(day=1)
        if step < 0:
            return (first - timedelta(days=1)).replace(day=1)
        return (first + timedelta(days=32)).replace(day=1)
    return calendar_grid.monday_of(day) + timedelta(days=7 * step)


@router.get("")
async def calendar_page(
    request: Request,
    view: str = "",
    date: str = "",
    employee_id: int = 0,
    cancelled: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    employees = visible_employees(db, user)
    # Сотруднику без «Видит всех» удобнее своя неделя, остальным — день всех
    if view not in VIEWS:
        view = "week" if own_employee_id(db, user) is not None else "day"
    day = _parse_date(date) or local_now().date()
    chosen = next((e for e in employees if e.id == employee_id), None)
    if view == "week" and chosen is None and employees:
        chosen = employees[0]
    shown = [chosen] if chosen else employees
    show_cancelled = bool(cancelled)

    context: dict = {}
    if view == "day":
        context["grid"] = calendar_grid.day_view(db, day, shown, show_cancelled=show_cancelled)
        title = calendar_grid.day_title(day)
    elif view == "week":
        if chosen is not None:
            context["grid"] = calendar_grid.week_view(db, day, chosen, show_cancelled=show_cancelled)
        title = calendar_grid.week_title(calendar_grid.monday_of(day))
    elif view == "week_all":
        monday = calendar_grid.monday_of(day)
        context["rows"] = calendar_grid.week_all_view(db, day, shown, show_cancelled=show_cancelled)
        context["days"] = [monday + timedelta(days=i) for i in range(7)]
        title = calendar_grid.week_title(monday)
    else:
        context["weeks"] = calendar_grid.month_view(db, day, shown)
        title = calendar_grid.month_title(day)

    params = {
        "view": view,
        "date": day.isoformat(),
        "employee_id": chosen.id if chosen else 0,
        "cancelled": 1 if show_cancelled else 0,
    }

    def link(**changes) -> str:
        merged = {**params, **changes}
        return "/admin/calendar?" + urlencode({k: v for k, v in merged.items() if v not in (0, "", None)})

    google_on = calendar_service.google_enabled(db)
    google_calendars = {}
    if google_on:
        for e in employees:
            cal = calendar_service.default_calendar(db, e.id)
            if cal is not None:
                google_calendars[e.id] = cal.calendar_name

    return render(
        request,
        "calendar/index.html",
        {
            **context,
            "user": user,
            "nav": "calendar",
            "wide": True,
            "view": view,
            "views": VIEWS,
            "title": title,
            "day": day,
            "today_date": local_now().date(),
            "employees": employees,
            "chosen": chosen,
            "show_cancelled": show_cancelled,
            "link": link,
            "back": link(),
            "prev_link": link(date=_shift(view, day, -1).isoformat()),
            "next_link": link(date=_shift(view, day, 1).isoformat()),
            "today_link": link(date=local_now().date().isoformat()),
            "can_create": user.has_permission("create_booking"),
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "weekday_short": calendar_grid.WEEKDAY_SHORT,
            "google_on": google_on,
            "google_calendars": google_calendars,
            "page_title": "Календарь",
        },
    )
