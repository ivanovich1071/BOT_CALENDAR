from datetime import time

from sqlalchemy import Boolean, ForeignKey, Integer, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Schedule(Base):
    """Недельный шаблон рабочих часов сотрудника (0 = понедельник … 6 = воскресенье)."""

    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("employee_id", "weekday", name="uq_employee_weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Пн … 6=Вс
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    employee = relationship("Employee", back_populates="schedules")
