from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import BOOKING_STATUS_LABELS_RU, BOOKING_STATUSES, SOURCE_ADMIN
from app.models.service import Service
from app.services import booking_service
from app.services.audit_service import log_action
from app.services.schedule_service import free_slots, local_tz

router = APIRouter(prefix="/admin/bookings")


def _parse_date(v: str):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


@router.get("")
async def list_bookings(
    request: Request,
    date: str = "",
    employee_id: int = 0,
    status: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    day = _parse_date(date) or datetime.now(local_tz()).date()
    start_utc = datetime.combine(day, datetime.min.time(), tzinfo=local_tz()).astimezone(ZoneInfo("UTC"))
    end_utc = start_utc + timedelta(days=1)

    q = (
        select(Booking)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.employee),
            selectinload(Booking.service),
        )
        .where(Booking.start_at < end_utc, Booking.end_at > start_utc)
        .order_by(Booking.start_at)
    )
    if employee_id:
        q = q.where(Booking.employee_id == employee_id)
    if status:
        q = q.where(Booking.status == status)
    bookings = db.scalars(q).all()
    employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
    ).all()
    return render(
        request,
        "bookings/list.html",
        {
            "user": user,
            "nav": "bookings",
            "items": bookings,
            "employees": employees,
            "statuses": BOOKING_STATUSES,
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "day": day.isoformat(),
            "f_employee": employee_id,
            "f_status": status,
            "page_title": "Записи",
        },
    )


@router.get("/new")
async def new_booking(
    request: Request,
    employee_id: int = 0,
    service_id: int = 0,
    user=Depends(require_permission("create_booking")),
    db: Session = Depends(get_db),
):
    employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
    ).all()
    services = db.scalars(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.name)
    ).all()
    clients = db.scalars(select(Client).order_by(Client.name.nulls_last()).limit(200)).all()
    return render(
        request,
        "bookings/form.html",
        {
            "user": user,
            "nav": "bookings",
            "employees": employees,
            "services": services,
            "clients": clients,
            "f_employee": employee_id or (employees[0].id if employees else 0),
            "f_service": service_id or (services[0].id if services else 0),
            "statuses": BOOKING_STATUS_LABELS_RU,
            "page_title": "Новая запись",
        },
    )


@router.get("/slots")
async def slots(
    request: Request,
    employee_id: int = 0,
    service_id: int = 0,
    date: str = "",
    exclude_booking_id: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HTMX-фрагмент со свободными слотами."""
    if not user.has_permission("view_calendar"):
        return render(request, "bookings/_slots.html", {"slots": [], "error": "Недостаточно прав"})
    service = db.get(Service, service_id)
    day = _parse_date(date)
    if service is None or day is None or employee_id == 0:
        return render(
            request,
            "bookings/_slots.html",
            {"slots": [], "error": "Выберите сотрудника, услугу и дату"},
        )
    # Занятость Google Calendar подставляется на этапе интеграции (CalendarService.freebusy)
    busy: list = []
    try:
        slot_list = free_slots(db, employee_id, service.duration_minutes, day, busy, exclude_booking_id)
    except Exception:  # noqa: BLE001
        return render(request, "bookings/_slots.html", {"slots": [], "error": "Ошибка расчёта слотов"})
    return render(
        request,
        "bookings/_slots.html",
        {"slots": slot_list, "error": "Свободных слотов нет" if not slot_list else None},
    )


@router.post("/create")
async def create_booking_post(
    request: Request,
    user=Depends(require_permission("create_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    employee_id = int(form.get("employee_id") or 0)
    service_id = int(form.get("service_id") or 0)
    client_id = int(form.get("client_id") or 0)
    day = _parse_date(form.get("date") or "")
    slot = (form.get("slot") or "").strip()
    notes = (form.get("notes") or "").strip() or None
    back = f"/admin/bookings/new?employee_id={employee_id}&service_id={service_id}"

    if not (employee_id and service_id and day and slot):
        return redirect(back, err="Заполните все поля")
    if not client_id:
        name = (form.get("client_name") or "").strip()
        if not name:
            return redirect(back, err="Выберите клиента или укажите имя нового")
        client = Client(name=name, phone=(form.get("client_phone") or "").strip() or None)
        db.add(client)
        db.commit()
        client_id = client.id

    try:
        hh, mm = slot.split(":")
        start_local = datetime.combine(day, datetime.min.time(), tzinfo=local_tz()).replace(
            hour=int(hh), minute=int(mm)
        )
        booking = booking_service.create_booking(
            db,
            client_id=client_id,
            employee_id=employee_id,
            service_id=service_id,
            start_at=start_local,
            source=SOURCE_ADMIN,
            notes=notes,
        )
    except booking_service.SlotTakenError:
        return redirect(back, err="Слот уже занят")
    except booking_service.NotFoundError as e:
        return redirect(back, err=str(e))
    except ValueError:
        return redirect(back, err="Неверное время")

    log_action(
        db, actor=user.login, action="booking.create", entity_type="booking",
        entity_id=booking.id, user_id=user.id,
        details={"start": booking.start_at.isoformat(), "source": SOURCE_ADMIN},
    )
    return redirect(f"/admin/bookings?date={day.isoformat()}", ok="Запись создана")


@router.get("/{booking_id}/reschedule")
async def reschedule_form(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)
    if booking is None:
        return redirect("/admin/bookings", err="Запись не найдена")
    return render(
        request,
        "bookings/reschedule.html",
        {
            "user": user,
            "nav": "bookings",
            "booking": booking,
            "page_title": f"Перенос записи #{booking.id}",
        },
    )


@router.post("/{booking_id}/reschedule")
async def reschedule_post(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    day = _parse_date(form.get("date") or "")
    slot = (form.get("slot") or "").strip()
    if not day or not slot:
        return redirect(f"/admin/bookings/{booking_id}/reschedule", err="Укажите дату и время")
    try:
        hh, mm = slot.split(":")
        start_local = datetime.combine(day, datetime.min.time(), tzinfo=local_tz()).replace(
            hour=int(hh), minute=int(mm)
        )
        booking_service.reschedule_booking(db, booking_id, new_start=start_local)
    except booking_service.SlotTakenError:
        return redirect(f"/admin/bookings/{booking_id}/reschedule", err="Слот уже занят")
    except booking_service.NotFoundError:
        return redirect("/admin/bookings", err="Активная запись не найдена")
    log_action(
        db, actor=user.login, action="booking.reschedule", entity_type="booking",
        entity_id=booking_id, user_id=user.id,
    )
    return redirect(f"/admin/bookings?date={day.isoformat()}", ok="Запись перенесена")


@router.post("/{booking_id}/status")
async def change_status(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    status = form.get("status") or ""
    if status not in BOOKING_STATUSES:
        return redirect("/admin/bookings", err="Неверный статус")
    try:
        booking_service.set_booking_status(db, booking_id, status)
    except booking_service.NotFoundError:
        return redirect("/admin/bookings", err="Запись не найдена")
    log_action(
        db, actor=user.login, action=f"booking.{status}", entity_type="booking",
        entity_id=booking_id, user_id=user.id,
    )
    return redirect("/admin/bookings", ok="Статус обновлён")
