from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.employee_service import employee_services


class Employee(Base):
    """Сотрудник/специалист. Может не иметь логина (только запись в расписании)."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # О специалисте: показывается клиенту и уходит ИИ-консультанту
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Telegram сотрудника — сюда бот шлёт уведомления о его записях
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    # Явно выбранный рабочий календарь. Пусто — берётся primary подключённого аккаунта.
    default_calendar_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # «Демо-специалист»: живёт только в админке, бот и ИИ его не видят
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="employee", foreign_keys=[user_id])
    schedules = relationship("Schedule", back_populates="employee")
    exceptions = relationship("ScheduleException", back_populates="employee")
    bookings = relationship("Booking", back_populates="employee")
    google_accounts = relationship("GoogleAccount", back_populates="employee")
    # Пусто — сотрудник оказывает все услуги
    services = relationship("Service", secondary=employee_services, back_populates="employees")
