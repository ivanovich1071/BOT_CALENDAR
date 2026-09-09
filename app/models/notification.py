from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Notification(Base):
    """Отправленное уведомление по записи.

    Строка появляется ТОЛЬКО после успешной отправки, а пара (booking_id, kind)
    уникальна — это и есть защита от повторной отсылки одного напоминания.
    """

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("booking_id", "kind", name="uq_notification_booking_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # reminder_24h, reminder_1h, created…
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
