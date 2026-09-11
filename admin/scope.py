"""Область видимости: пользователь без права «Видит всех» работает только со своим.

«Своё» — записи, расписание и клиенты сотрудника, к которому привязан логин.
Логин без привязки к сотруднику и без права «Видит всех» не видит ничего.
"""

from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from admin.templating import render
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import PERM_VIEW_ALL
from app.models.user import User

FOREIGN = "Это данные другого сотрудника"


def own_employee_id(db: Session, user: User) -> int | None:
    """None — видит всех; id сотрудника — только его; 0 — сотрудника нет, не видит ничего."""
    if user.has_permission(PERM_VIEW_ALL):
        return None
    return db.scalar(select(Employee.id).where(Employee.user_id == user.id)) or 0


def can_touch(db: Session, user: User, employee_id: int | None) -> bool:
    own = own_employee_id(db, user)
    return own is None or (own != 0 and own == employee_id)


def own_clients_clause(own: int):
    """Клиенты, которые хоть раз записывались к сотруднику own."""
    return Client.id.in_(select(Booking.client_id).where(Booking.employee_id == own))


def client_visible(db: Session, user: User, client_id: int) -> bool:
    own = own_employee_id(db, user)
    if own is None:
        return True
    return bool(db.scalar(select(exists().where(Booking.client_id == client_id, Booking.employee_id == own))))


def forbidden(request: Request, user: User, message: str = FOREIGN):
    return render(request, "error.html", {"user": user, "message": message}, 403)
