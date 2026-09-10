from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from admin.templating import render
from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import BOOKED, BOOKING_STATUS_LABELS_RU
from app.models.service import Service
from app.services.schedule_service import local_tz

router = APIRouter(prefix="/admin")


@router.get("")
@router.get("/")
async def dashboard(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tz = local_tz()
    now_local = datetime.now(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    week_end = day_start + timedelta(days=7)
    day_start_utc = day_start.astimezone(ZoneInfo("UTC"))
    day_end_utc = day_end.astimezone(ZoneInfo("UTC"))
    week_end_utc = week_end.astimezone(ZoneInfo("UTC"))

    today_bookings = db.scalars(
        select(Booking)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.employee),
            selectinload(Booking.service),
        )
        .where(
            Booking.status == BOOKED,
            Booking.start_at < day_end_utc,
            Booking.end_at > day_start_utc,
        )
        .order_by(Booking.start_at)
    ).all()

    stats = {
        "today": len(today_bookings),
        "week": db.scalar(
            select(func.count(Booking.id)).where(
                Booking.status == BOOKED,
                Booking.start_at < week_end_utc,
                Booking.end_at > day_start_utc,
            )
        )
        or 0,
        "clients": db.scalar(select(func.count(Client.id))) or 0,
        "employees": db.scalar(
            select(func.count(Employee.id)).where(Employee.is_active.is_(True))
        )
        or 0,
        "services": db.scalar(
            select(func.count(Service.id)).where(Service.is_active.is_(True))
        )
        or 0,
    }

    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "nav": "dashboard",
            "stats": stats,
            "today_bookings": today_bookings,
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "page_title": "Панель управления",
        },
    )
