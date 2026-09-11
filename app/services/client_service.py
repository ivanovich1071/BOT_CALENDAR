"""Клиенты: удаление вместе с записями и диалогами — по запросу на удаление данных."""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.booking import Booking
from app.models.client import Client
from app.models.enums import BOOKED
from app.services import booking_flow, booking_service
from app.services.audit_service import log_action


def delete_client(db: Session, client_id: int, *, actor: str, user_id: int | None = None) -> dict:
    """Удаляет клиента, его записи и переписку с ИИ-консультантом.

    Будущие активные записи сначала отменяются — их события уходят из Google.
    В журнал пишутся только числа: имя и телефон удалённого клиента не сохраняются.
    """
    if db.get(Client, client_id) is None:
        raise booking_service.NotFoundError("Клиент не найден")
    now = datetime.now(timezone.utc)
    bookings = db.scalars(select(Booking).where(Booking.client_id == client_id)).all()
    upcoming = [b.id for b in bookings if b.status == BOOKED and b.start_at > now]
    for booking_id in upcoming:
        booking_flow.cancel(db, booking_id, actor=actor, user_id=user_id)

    db.execute(delete(Booking).where(Booking.client_id == client_id))
    # Реплики диалогов удаляются каскадом по внешнему ключу
    db.delete(db.get(Client, client_id))
    db.commit()
    result = {"bookings": len(bookings), "cancelled": len(upcoming)}
    log_action(
        db, actor=actor, action="client.delete", entity_type="client",
        entity_id=client_id, details=result, user_id=user_id,
    )
    return result
