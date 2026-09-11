"""Услуги: карточка с ценой, исполнителями и порядком; архив и удаление."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from admin.diff import changed_fields
from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.bot.services import price_label
from app.db.database import get_db
from app.models.booking import Booking
from app.models.employee import Employee
from app.models.employee_service import employee_services
from app.models.service import Service
from app.models.user import User
from app.services.app_settings_service import COMPANY, get_setting
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/services")

MIN_DURATION, MAX_DURATION = 5, 600


def _performs(employee: Employee, service: Service | None) -> bool:
    """У сотрудника без привязанных услуг — все услуги."""
    return not employee.services or (service is not None and service in employee.services)


def _employees(db: Session) -> list[Employee]:
    return db.scalars(
        select(Employee)
        .options(selectinload(Employee.services))
        .where(Employee.archived_at.is_(None))
        .order_by(Employee.name)
    ).all()


def _snapshot(s: Service) -> dict:
    return {
        "name": s.name,
        "description": s.description,
        "duration_minutes": s.duration_minutes,
        "price": str(s.price) if s.price is not None else None,
        "sort_order": s.sort_order,
        "is_active": s.is_active,
    }


def parse_price(mode: str, raw: str) -> Decimal | None:
    """Режим цены из формы: сумма, «бесплатно» (0) или «по договорённости» (NULL)."""
    if mode == "free":
        return Decimal(0)
    if mode == "negotiable":
        return None
    try:
        value = Decimal(raw.strip().replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Цена: число, например 1500") from exc
    if value < 0:
        raise ValueError("Цена не может быть отрицательной")
    return value


def set_performers(db: Session, service: Service, employee_ids: set[int]) -> str | None:
    """Кто проводит услугу. Возвращает текст ошибки или None.

    Сотрудник без привязок проводит всё; если с него снимают эту услугу, его
    привязки становятся явными — все остальные действующие услуги.
    """
    all_services = db.scalars(select(Service).where(Service.archived_at.is_(None))).all()
    for employee in _employees(db):
        want = employee.id in employee_ids
        if want == _performs(employee, service):
            continue
        if want:
            employee.services = [*employee.services, service]
            continue
        base = employee.services or all_services
        rest = [s for s in base if s.id != service.id]
        if not rest:
            return f"У сотрудника «{employee.name}» не останется ни одной услуги — архивируйте его или добавьте ему услуги"
        employee.services = rest
    return None


@router.get("")
async def list_services(
    request: Request,
    archived: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("manage_services") and not user.has_permission("create_booking"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    services = db.scalars(
        select(Service)
        .where(Service.archived_at.is_not(None) if archived else Service.archived_at.is_(None))
        .order_by(Service.sort_order, Service.name)
    ).all()
    employees = _employees(db)
    currency = get_setting(db, COMPANY).get("currency") or ""
    items = [
        {
            "service": s,
            "price": price_label(s.price, currency),
            "performers": [e.name for e in employees if _performs(e, s)],
        }
        for s in services
    ]
    return render(
        request,
        "services/list.html",
        {
            "user": user,
            "nav": "services",
            "items": items,
            "archived": archived,
            "can_manage": user.has_permission("manage_services"),
            "page_title": "Услуги",
        },
    )


def _form(request: Request, db: Session, user: User, service: Service | None):
    employees = _employees(db)
    bookings = db.scalar(select(func.count(Booking.id)).where(Booking.service_id == service.id)) if service else 0
    if service is None or service.price is not None and service.price > 0:
        price_mode = "fixed"
    elif service.price is None:
        price_mode = "negotiable"
    else:
        price_mode = "free"
    return render(
        request,
        "services/form.html",
        {
            "user": user,
            "nav": "services",
            "service": service,
            "employees": employees,
            "performing": {e.id for e in employees if _performs(e, service)},
            "price_mode": price_mode,
            "bookings_count": bookings or 0,
            "page_title": f"Услуга: {service.name}" if service else "Новая услуга",
        },
    )


@router.get("/new")
async def new_service(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_services")),
):
    return _form(request, db, user, None)


@router.get("/{service_id}/edit")
async def edit_service(
    service_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service is None:
        return redirect("/admin/services", err="Услуга не найдена")
    return _form(request, db, user, service)


@router.post("/save")
async def save_service(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    form = await request.form()
    service_id = int(form.get("service_id") or 0)
    back = f"/admin/services/{service_id}/edit" if service_id else "/admin/services/new"
    name = str(form.get("name") or "").strip()
    if not name or len(name) > 120:
        return redirect(back, err="Название обязательно, до 120 символов")
    try:
        duration = int(str(form.get("duration_minutes") or "").strip())
        order = int(str(form.get("sort_order") or "0").strip() or 0)
    except ValueError:
        return redirect(back, err="Длительность и порядок — целые числа")
    if not MIN_DURATION <= duration <= MAX_DURATION:
        return redirect(back, err=f"Длительность — от {MIN_DURATION} до {MAX_DURATION} минут")
    try:
        price = parse_price(str(form.get("price_mode") or "fixed"), str(form.get("price") or ""))
    except ValueError as exc:
        return redirect(back, err=str(exc))
    performer_ids = {int(v) for v in form.getlist("employees") if str(v).isdigit()}

    if service_id:
        service = db.get(Service, service_id)
        if service is None:
            return redirect("/admin/services", err="Услуга не найдена")
        before = _snapshot(service)
    else:
        service = Service(name=name)
        db.add(service)
        before = {}
    service.name = name
    service.description = str(form.get("description") or "").strip() or None
    service.duration_minutes = duration
    service.price = price
    service.sort_order = order
    service.is_active = form.get("is_active") == "on"
    db.flush()
    error = set_performers(db, service, performer_ids)
    if error:
        db.rollback()
        return redirect(back, err=error)
    db.commit()
    log_action(
        db, actor=user.login, action="service.update" if service_id else "service.create",
        entity_type="service", entity_id=service.id, details=changed_fields(before, _snapshot(service)), user_id=user.id,
    )
    return redirect("/admin/services", ok="Услуга сохранена")


@router.post("/{service_id}/toggle")
async def toggle_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service:
        service.is_active = not service.is_active
        db.commit()
        log_action(db, actor=user.login, action="service.toggle", entity_type="service", entity_id=service_id, details={"active": service.is_active}, user_id=user.id)
    return redirect("/admin/services", ok="Сохранено")


@router.post("/{service_id}/archive")
async def archive_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service is None:
        return redirect("/admin/services", err="Услуга не найдена")
    service.archived_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, actor=user.login, action="service.archive", entity_type="service", entity_id=service_id, user_id=user.id)
    return redirect("/admin/services", ok=f"Услуга «{service.name}» в архиве — записи на неё сохранены")


@router.post("/{service_id}/restore")
async def restore_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service is None:
        return redirect("/admin/services", err="Услуга не найдена")
    service.archived_at = None
    db.commit()
    log_action(db, actor=user.login, action="service.restore", entity_type="service", entity_id=service_id, user_id=user.id)
    return redirect("/admin/services", ok=f"Услуга «{service.name}» восстановлена")


@router.post("/{service_id}/delete")
async def delete_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service is None:
        return redirect("/admin/services", err="Услуга не найдена")
    bookings = db.scalar(select(func.count(Booking.id)).where(Booking.service_id == service_id)) or 0
    if bookings:
        return redirect(f"/admin/services/{service_id}/edit", err=f"На услугу есть записи ({bookings}) — перенесите её в архив")
    name = service.name
    db.execute(delete(employee_services).where(employee_services.c.service_id == service_id))
    db.execute(delete(Service).where(Service.id == service_id))
    db.commit()
    db.expire_all()
    log_action(db, actor=user.login, action="service.delete", entity_type="service", entity_id=service_id, details={"name": name}, user_id=user.id)
    return redirect("/admin/services", ok=f"Услуга «{name}» удалена")
