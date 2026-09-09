from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import get_current_user, require_permission
from app.db.database import get_db
from app.models.client import Client
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/clients")


@router.get("")
async def list_clients(
    request: Request,
    q: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_clients"):
        return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)
    query = select(Client).order_by(Client.created_at.desc()).limit(300)
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
        {"user": user, "nav": "clients", "items": clients, "q": q, "page_title": "Клиенты"},
    )


@router.post("/{client_id}/update")
async def update_client(
    client_id: int,
    name: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("view_clients")),
):
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/clients", err="Клиент не найден")
    client.name = name.strip() or None
    client.phone = phone.strip() or None
    client.notes = notes.strip() or None
    db.commit()
    log_action(db, actor=user.login, action="client.update", entity_type="client", entity_id=client_id, user_id=user.id)
    return redirect("/admin/clients", ok="Сохранено")
