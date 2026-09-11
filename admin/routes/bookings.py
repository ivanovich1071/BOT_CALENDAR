from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect, safe_back
from admin.scope import FOREIGN, can_touch, forbidden, own_clients_clause, own_employee_id, visible_employees
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKING_STATUS_LABELS_RU, BOOKING_STATUSES, SOURCE_ADMIN
from app.models.service import Service
from app.services import booking_flow, booking_service
from app.services.schedule_service import local_tz

router = APIRouter(prefix="/admin/bookings")


def _parse_date(v: str):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _created_message(google) -> str:
    """Google молчит (None) — так и задумано: система работает и без календаря."""
    if google is False:
        return "Запись создана, но событие в Google Calendar не создано — проверьте журнал"
    return "Запись создана"


def _day_list(booking: Booking) -> str:
    return f"/admin/bookings?date={booking.start_at.astimezone(local_tz()).date().isoformat()}"


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
    own = own_employee_id(db, user)
    if own is not None:
        q = q.where(Booking.employee_id == own)
    if employee_id:
        q = q.where(Booking.employee_id == employee_id)
    if status:
        q = q.where(Booking.status == status)
    bookings = db.scalars(q).all()
    return render(
        request,
        "bookings/list.html",
        {
            "user": user,
            "nav": "bookings",
            "items": bookings,
            "employees": visible_employees(db, user),
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
    date: str = "",
    slot: str = "",
    back: str = "",
    user=Depends(require_permission("create_booking")),
    db: Session = Depends(get_db),
):
    employees = visible_employees(db, user)
    services = db.scalars(
        select(Service)
        .where(Service.is_active.is_(True), Service.archived_at.is_(None))
        .order_by(Service.sort_order, Service.name)
    ).all()
    clients_q = select(Client).where(Client.archived_at.is_(None)).order_by(Client.name.nulls_last()).limit(200)
    own = own_employee_id(db, user)
    if own is not None:
        clients_q = clients_q.where(own_clients_clause(own))
    clients = db.scalars(clients_q).all()
    day = _parse_date(date)
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
            # Из календаря приходят день и время ячейки — форма сразу показывает слоты
            "f_date": day.isoformat() if day else "",
            "f_slot": slot.strip()[:5],
            "back": safe_back(back),
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
    selected: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HTMX-фрагмент со свободными слотами (расписание − брони − занятость Google)."""
    if not user.has_permission("view_calendar") or not can_touch(db, user, employee_id):
        return render(request, "bookings/_slots.html", {"slots": [], "error": "Недостаточно прав"})
    day = _parse_date(date)
    if day is None or employee_id == 0 or service_id == 0:
        return render(
            request,
            "bookings/_slots.html",
            {"slots": [], "error": "Выберите сотрудника, услугу и дату"},
        )
    try:
        slot_list = booking_flow.available_slots(
            db, employee_id, service_id, day, exclude_booking_id or None
        )
    except Exception:  # noqa: BLE001
        return render(request, "bookings/_slots.html", {"slots": [], "error": "Ошибка расчёта слотов"})
    selected = selected.strip()[:5]
    return render(
        request,
        "bookings/_slots.html",
        {
            "slots": slot_list,
            "selected": selected,
            # Время из ячейки календаря может не подойти услуге: длинная не влезет до перерыва
            "selected_missing": bool(selected) and selected not in {s.strftime("%H:%M") for s in slot_list},
        },
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
    back = safe_back(form.get("back"))
    retry = "/admin/bookings/new?" + urlencode(
        {
            "employee_id": employee_id,
            "service_id": service_id,
            "date": day.isoformat() if day else "",
            "slot": slot,
            "back": back or "",
        }
    )

    if not (employee_id and service_id and day and slot):
        return redirect(retry, err="Заполните все поля")
    if not can_touch(db, user, employee_id):
        return redirect(retry, err=FOREIGN)
    if not client_id:
        name = (form.get("client_name") or "").strip()
        if not name:
            return redirect(retry, err="Выберите клиента или укажите имя нового")
        client = Client(name=name, phone=(form.get("client_phone") or "").strip() or None)
        db.add(client)
        db.commit()
        client_id = client.id

    try:
        hh, mm = slot.split(":")
        start_local = datetime.combine(day, datetime.min.time(), tzinfo=local_tz()).replace(
            hour=int(hh), minute=int(mm)
        )
        _booking, google = booking_flow.create(
            db,
            client_id=client_id,
            employee_id=employee_id,
            service_id=service_id,
            start_at=start_local,
            source=SOURCE_ADMIN,
            notes=notes,
            actor=user.login,
            user_id=user.id,
        )
    except booking_service.SlotTakenError:
        return redirect(retry, err="Слот уже занят")
    except booking_service.NotFoundError as e:
        return redirect(retry, err=str(e))
    except ValueError:
        return redirect(retry, err="Неверное время")

    return redirect(back or f"/admin/bookings?date={day.isoformat()}", ok=_created_message(google))


@router.get("/{booking_id}/reschedule")
async def reschedule_form(
    booking_id: int,
    request: Request,
    back: str = "",
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)
    if booking is None:
        return redirect("/admin/bookings", err="Запись не найдена")
    if not can_touch(db, user, booking.employee_id):
        return forbidden(request, user)
    return render(
        request,
        "bookings/reschedule.html",
        {
            "user": user,
            "nav": "bookings",
            "booking": booking,
            "back": safe_back(back),
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
    booking = db.get(Booking, booking_id)
    if booking is None:
        return redirect("/admin/bookings", err="Запись не найдена")
    form = await request.form()
    back = safe_back(form.get("back"))
    if not can_touch(db, user, booking.employee_id):
        return redirect(back or "/admin/bookings", err=FOREIGN)
    retry = f"/admin/bookings/{booking_id}/reschedule" + (f"?back={quote(back)}" if back else "")
    day = _parse_date(form.get("date") or "")
    slot = (form.get("slot") or "").strip()
    if not day or not slot:
        return redirect(retry, err="Укажите дату и время")
    try:
        hh, mm = slot.split(":")
        start_local = datetime.combine(day, datetime.min.time(), tzinfo=local_tz()).replace(
            hour=int(hh), minute=int(mm)
        )
        _booking, google = booking_flow.reschedule(
            db, booking_id, new_start=start_local, actor=user.login, user_id=user.id
        )
    except booking_service.SlotTakenError:
        return redirect(retry, err="Слот уже занят")
    except booking_service.NotFoundError:
        return redirect(back or "/admin/bookings", err="Активная запись не найдена")
    except ValueError:
        return redirect(retry, err="Неверное время")

    message = (
        "Запись перенесена, но событие в Google Calendar осталось на прежнем месте"
        if google is False
        else "Запись перенесена"
    )
    return redirect(back or f"/admin/bookings?date={day.isoformat()}", ok=message)


@router.post("/{booking_id}/status")
async def change_status(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    back = safe_back(form.get("back"))
    status = form.get("status") or ""
    if status not in BOOKING_STATUSES:
        return redirect(back or "/admin/bookings", err="Неверный статус")
    current = db.get(Booking, booking_id)
    if current is None:
        return redirect(back or "/admin/bookings", err="Запись не найдена")
    if not can_touch(db, user, current.employee_id):
        return redirect(back or "/admin/bookings", err=FOREIGN)
    try:
        booking, google = booking_flow.set_status(
            db, booking_id, status, actor=user.login, user_id=user.id
        )
    except booking_service.NotFoundError:
        return redirect(back or "/admin/bookings", err="Запись не найдена")
    except booking_service.SlotTakenError:
        return redirect(back or "/admin/bookings", err="Вернуть нельзя: это время уже занято")

    if google is False:
        message = (
            "Запись возвращена, но событие в Google Calendar не создано"
            if status == "booked"
            else "Статус обновлён, но событие в Google Calendar не удалено"
        )
    else:
        message = "Запись возвращена" if status == "booked" else "Статус обновлён"
    return redirect(back or _day_list(booking), ok=message)


# ==== Карточка записи: правка всех полей и удаление ====

@router.get("/{booking_id}")
async def booking_card(
    booking_id: int,
    request: Request,
    back: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_calendar"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    booking = db.get(Booking, booking_id)
    if booking is None:
        return redirect("/admin/bookings", err="Запись не найдена")
    if not can_touch(db, user, booking.employee_id):
        return forbidden(request, user)

    # В выпадающих списках — доступные плюс текущие значения записи, даже если они в архиве
    employees = visible_employees(db, user)
    if booking.employee not in employees:
        employees = [booking.employee, *employees]
    services = db.scalars(
        select(Service).where(Service.archived_at.is_(None)).order_by(Service.sort_order, Service.name)
    ).all()
    if booking.service not in services:
        services = [booking.service, *services]
    clients_q = select(Client).where(Client.archived_at.is_(None)).order_by(Client.name.nulls_last()).limit(300)
    own = own_employee_id(db, user)
    if own is not None:
        clients_q = clients_q.where(own_clients_clause(own))
    clients = db.scalars(clients_q).all()
    if booking.client not in clients:
        clients = [booking.client, *clients]
    history = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "booking", AuditLog.entity_id == str(booking_id))
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    ).all()
    start_local = booking.start_at.astimezone(local_tz())
    return render(
        request,
        "bookings/card.html",
        {
            "user": user,
            "nav": "bookings",
            "booking": booking,
            "employees": employees,
            "services": services,
            "clients": clients,
            "history": history,
            "date": start_local.date().isoformat(),
            "time": start_local.strftime("%H:%M"),
            "back": safe_back(back),
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "can_edit": user.has_permission("edit_booking"),
            "can_delete": user.has_permission("delete_booking"),
            "page_title": f"Запись #{booking.id}",
        },
    )


@router.post("/{booking_id}/update")
async def update_booking_post(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("edit_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    back = safe_back(form.get("back"))
    card = f"/admin/bookings/{booking_id}" + (f"?back={quote(back)}" if back else "")
    current = db.get(Booking, booking_id)
    if current is None:
        return redirect(back or "/admin/bookings", err="Запись не найдена")
    day = _parse_date(form.get("date") or "")
    try:
        employee_id = int(form.get("employee_id") or 0)
        service_id = int(form.get("service_id") or 0)
        client_id = int(form.get("client_id") or 0)
        hh, mm = str(form.get("time") or "").strip().split(":")
        start_local = datetime.combine(day, time(int(hh), int(mm)), tzinfo=local_tz())
    except (TypeError, ValueError):
        return redirect(card, err="Укажите сотрудника, услугу, клиента, дату и время")
    if not can_touch(db, user, current.employee_id) or not can_touch(db, user, employee_id):
        return redirect(card, err=FOREIGN)
    notes = str(form.get("notes") or "").strip() or None
    try:
        _booking, google = booking_flow.update(
            db,
            booking_id,
            client_id=client_id,
            employee_id=employee_id,
            service_id=service_id,
            start_at=start_local,
            notes=notes,
            actor=user.login,
            user_id=user.id,
        )
    except (booking_service.SlotTakenError, booking_service.NotFoundError, booking_service.BookingError) as exc:
        return redirect(card, err=str(exc))
    message = "Запись сохранена"
    if google is False:
        message += ", но Google Calendar не обновился — проверьте журнал"
    return redirect(card, ok=message)


@router.post("/{booking_id}/delete")
async def delete_booking_post(
    booking_id: int,
    request: Request,
    user=Depends(require_permission("delete_booking")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    back = safe_back(form.get("back"))
    booking = db.get(Booking, booking_id)
    if booking is None:
        return redirect(back or "/admin/bookings", err="Запись не найдена")
    if not can_touch(db, user, booking.employee_id):
        return redirect(back or "/admin/bookings", err=FOREIGN)
    day_list = _day_list(booking)
    google = booking_flow.delete(db, booking_id, actor=user.login, user_id=user.id)
    message = "Запись удалена"
    if google is False:
        message += ", но событие в Google Calendar не удалено — проверьте журнал"
    return redirect(back or day_list, ok=message)
