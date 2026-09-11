"""Диалоги клиентов с ИИ-консультантом: что спрашивали и что ответил бот."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from admin.flash import redirect
from admin.scope import client_visible, forbidden, own_employee_id
from admin.templating import render
from app.ai.agent import HISTORY_RETENTION_DAYS
from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.models.ai_message import AiMessage
from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKING_STATUS_LABELS_RU, SOURCE_AI

router = APIRouter(prefix="/admin/dialogs")


def _forbidden(request: Request, user):
    return render(request, "error.html", {"user": user, "message": "Недостаточно прав"}, 403)


@router.get("")
async def dialogs_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_clients"):
        return _forbidden(request, user)
    last = func.max(AiMessage.created_at)
    query = (
        select(AiMessage.client_id, func.count(AiMessage.id), last)
        .where(AiMessage.role.in_(("user", "assistant")))
        .group_by(AiMessage.client_id)
        .order_by(last.desc())
        .limit(200)
    )
    own = own_employee_id(db, user)
    if own is not None:
        query = query.where(AiMessage.client_id.in_(select(Booking.client_id).where(Booking.employee_id == own)))
    rows = db.execute(query).all()
    clients = {c.id: c for c in db.scalars(select(Client).where(Client.id.in_([r[0] for r in rows])))}
    items = [
        {"client": clients[client_id], "count": count, "last": last_at}
        for client_id, count, last_at in rows
        if client_id in clients
    ]
    return render(
        request,
        "dialogs/list.html",
        {
            "user": user,
            "nav": "dialogs",
            "items": items,
            "retention_days": HISTORY_RETENTION_DAYS,
            "page_title": "Диалоги",
        },
    )


@router.get("/{client_id}")
async def dialog_thread(
    client_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.has_permission("view_clients"):
        return _forbidden(request, user)
    client = db.get(Client, client_id)
    if client is None:
        return redirect("/admin/dialogs", err="Клиент не найден")
    if not client_visible(db, user, client_id):
        return forbidden(request, user)
    messages = db.scalars(
        select(AiMessage)
        .where(AiMessage.client_id == client_id)
        .order_by(AiMessage.created_at, AiMessage.id)
        .limit(500)
    ).all()
    bookings = db.scalars(
        select(Booking)
        .options(selectinload(Booking.employee), selectinload(Booking.service))
        .where(Booking.client_id == client_id, Booking.source == SOURCE_AI)
        .order_by(Booking.start_at.desc())
    ).all()
    return render(
        request,
        "dialogs/thread.html",
        {
            "user": user,
            "nav": "dialogs",
            "client": client,
            "messages": messages,
            "bookings": bookings,
            "status_labels": BOOKING_STATUS_LABELS_RU,
            "page_title": f"Диалог: {client.name or f'клиент #{client.id}'}",
        },
    )
