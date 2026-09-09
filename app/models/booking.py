from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Booking(Base):
    """Запись клиента. google_event_id связывает её с событием Google Calendar."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_employee_start", "employee_id", "start_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    calendar_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True
    )
    google_event_id: Mapped[str | None] = mapped_column(String(190), index=True, nullable=True)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="booked", index=True)
    source: Mapped[str] = mapped_column(String(20), default="telegram")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client = relationship("Client", back_populates="bookings")
    employee = relationship("Employee", back_populates="bookings")
    service = relationship("Service", back_populates="bookings")
    calendar = relationship("Calendar", back_populates="bookings")
