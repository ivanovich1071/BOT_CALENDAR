from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Calendar(Base):
    """Конкретный Google-календарь, привязанный к Google-аккаунту.

    Гибридная модель: у сотрудника может быть несколько календарей,
    один календарь может обслуживать нескольких сотрудников (через
    отдельные GoogleAccount-строки того же google_calendar_id).
    """

    __tablename__ = "calendars"
    __table_args__ = (
        UniqueConstraint("google_account_id", "google_calendar_id", name="uq_account_calendar"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    google_account_id: Mapped[int] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), index=True
    )
    google_calendar_id: Mapped[str] = mapped_column(String(190))
    calendar_name: Mapped[str] = mapped_column(String(190), default="Primary")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    google_account = relationship("GoogleAccount", back_populates="calendars")
    bookings = relationship("Booking", back_populates="calendar")
