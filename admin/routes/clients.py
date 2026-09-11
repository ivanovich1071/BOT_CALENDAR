from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect
from admin.scope import FOREIGN, client_visible, forbidden, own_clients_clause, own_employee_id
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.ai_message import AiMessage
from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import ADMIN, BOOKING_STATUS_LABELS_RU
from app.models.user import User
from app.services import booking_service, client_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/clients")


def _no_rights(request: Request, user):
    return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)


@router.get("")
async def list_clients(
    request: Request,
    q: str = "",
    archived: int = 0,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_clients"):
        return _no_rights(request, user)
    query = (
        select(Client)
        .where(Client.archived_at.is_not(None) if archived else Client.archived_at.is_(None))
        .order_by(Client.created_at.desc())
        .limit(300)
    )
    own = own_employee_id(db, user)
    if own is not None:
        query = query.where(own_clients_clause(own))
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Client.name.ilike(like),
                Client.phone.ilike(like),
                Client.telegram_username.ilike(like),
            )
        )
    clients = db.scalars(query).all()
    return render(
        request,
        "clients/list.html",
        {
            "user": user,
            "nav": "clients",
            "items": clients,
            "q": q,
            "archived": archived,
            "page_title": "Клиенты",
        },
    )


@router.get("/{client_id}")
async def client_card(
    client_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_clients"):
        return _no_rights(request, user)
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/clients", err="Клиент не найден")
    if not client_visible(db, user, client_id):
        return forbidden(request, user)
    bookings_q = (
        select(Booking)
        .options(selectinload(Booking.employee), selectinload(Booking.service))
        .where(Booking.client_id == client_id)
        .order_by(Booking.start_at.desc())
    )
    own = own_employee_id(db, user)
    if own is not None:
        bookings_q = bookings_q.where(Booking.employee_id == own)
    bookings = db.scalars(bookings_q).all()
    dialog_messages = db.scalar(select(func.count(AiMessage.id)).where(AiMessage.client_id == client_id)) or 0
    return render(
        request,
        "clients/card.html",
        {
            "user": user,
            "nav": "clients",
            "client": client,
            "bookings": bookings,
            "dialog_messages": dialog_messages,
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "can_delete": user.role == ADMIN,
            "page_title": client.name or f"Клиент #{client.id}",
        },
    )


@router.post("/{client_id}/update")
async def update_client(
    client_id: int,
    name: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("view_clients")),
):
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/clients", err="Клиент не найден")
    if not client_visible(db, user, client_id):
        return redirect("/admin/clients", err=FOREIGN)
    before = {"name": client.name, "phone": client.phone, "notes": client.notes}
    client.name = name.strip() or None
    client.phone = phone.strip() or None
    client.notes = notes.strip() or None
    db.commit()
    after = {"name": client.name, "phone": client.phone, "notes": client.notes}
    # В журнал — только какие поля менялись: значения — персональные данные
    log_action(
        db, actor=user.login, action="client.update", entity_type="client", entity_id=client_id,
        details={"fields": [k for k in after if after[k] != before[k]]}, user_id=user.id,
    )
    target = back if back.startswith("/admin/clients") else "/admin/clients"
    return redirect(target, ok="Сохранено")


@router.post("/{client_id}/archive")
async def archive_client(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("view_clients")),
):
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/clients", err="Клиент не найден")
    if not client_visible(db, user, client_id):
        return redirect("/admin/clients", err=FOREIGN)
    client.archived_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, actor=user.login, action="client.archive", entity_type="client", entity_id=client_id, user_id=user.id)
    return redirect(f"/admin/clients/{client_id}", ok="Клиент перенесён в архив")


@router.post("/{client_id}/restore")
async def restore_client(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("view_clients")),
):
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/clients", err="Клиент не найден")
    if not client_visible(db, user, client_id):
        return redirect("/admin/clients", err=FOREIGN)
    client.archived_at = None
    db.commit()
    log_action(db, actor=user.login, action="client.restore", entity_type="client", entity_id=client_id, user_id=user.id)
    return redirect(f"/admin/clients/{client_id}", ok="Клиент восстановлен")


@router.post("/{client_id}/delete")
async def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != ADMIN:
        return redirect(f"/admin/clients/{client_id}", err="Удалять клиентов может только администратор")
    try:
        result = client_service.delete_client(db, client_id, actor=user.login, user_id=user.id)
    except booking_service.NotFoundError:
        return redirect("/admin/clients", err="Клиент не найден")
    return redirect(
        "/admin/clients",
        ok=f"Клиент и его данные удалены: записей — {result['bookings']}, из них отменено будущих — {result['cancelled']}",
    )
