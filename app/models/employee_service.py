"""Кто какую услугу оказывает. У сотрудника без строк здесь — все услуги."""

from sqlalchemy import Column, ForeignKey, Table

from app.db.database import Base

employee_services = Table(
    "employee_services",
    Base.metadata,
    Column("employee_id", ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
)
