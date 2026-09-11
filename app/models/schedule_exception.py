from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, ForeignKey, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ScheduleException(Base):
    """Исключение из недельного расписания на диапазон дат.

    kind: day_off — не работает весь день (выходной, отпуск);
          block   — закрыто время start–end;
          extra   — дополнительное окно start–end (например, суббота).
    """

    __tablename__ = "schedule_exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    employee = relationship("Employee", back_populates="exceptions")
