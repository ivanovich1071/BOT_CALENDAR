from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.service import Service
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/services")


@router.get("")
async def list_services(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("manage_services") and not user.has_permission("create_booking"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    services = db.scalars(select(Service).order_by(Service.name)).all()
    return render(
        request,
        "services/list.html",
        {
            "user": user,
            "nav": "services",
            "items": services,
            "can_manage": user.has_permission("manage_services"),
            "page_title": "Услуги",
        },
    )


@router.post("/create")
async def create_service(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(30),
    price: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    price_value = None
    if price.strip():
        try:
            price_value = Decimal(price.strip().replace(",", "."))
        except InvalidOperation:
            return redirect("/admin/services", err="Неверная цена")
    db.add(
        Service(
            name=name.strip(),
            description=description.strip() or None,
            duration_minutes=max(5, duration_minutes),
            price=price_value,
        )
    )
    db.commit()
    log_action(db, actor=user.login, action="service.create", entity_type="service", details={"name": name.strip()}, user_id=user.id)
    return redirect("/admin/services", ok="Услуга создана")


@router.post("/{service_id}/update")
async def update_service(
    service_id: int,
    name: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(30),
    price: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_services")),
):
    service = db.get(Service, service_id)
    if service is None:
        return redirect("/admin/services", err="Услуга не найдена")
    service.name = name.strip()
    service.description = description.strip() or None
    service.duration_minutes = max(5, duration_minutes)
    if price.strip():
        try:
            service.price = Decimal(price.strip().replace(",", "."))
        except InvalidOperation:
            return redirect("/admin/services", err="Неверная цена")
    else:
        service.price = None
    service.is_active = is_active == "on"
    db.commit()
    log_action(db, actor=user.login, action="service.update", entity_type="service", entity_id=service_id, user_id=user.id)
    return redirect("/admin/services", ok="Сохранено")


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
