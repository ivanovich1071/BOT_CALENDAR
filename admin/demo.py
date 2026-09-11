"""Защита демо-доступа: гость смотрит всю админку, а меняет только демо-данные.

Запрещено всё, что не разрешено здесь явно: новая кнопка в админке не откроется
гостю, пока её сознательно не добавят в список.
"""

import re
from urllib.parse import quote_plus, urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from admin.flash import safe_back
from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.schedule_exception import ScheduleException
from app.models.user import User

DENIED = "В демо-доступе это недоступно: меняйте свои записи и всё у «Демо-специалиста»"

BOOKING_ACTION = re.compile(r"/admin/bookings/(\d+)/(update|reschedule|status|delete)")
EXCEPTION_DELETE = re.compile(r"/admin/schedule/exceptions/(\d+)/delete")
CLIENT_UPDATE = re.compile(r"/admin/clients/(\d+)/update")


def _to_int(value) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def _is_demo_employee(db: Session, employee_id: int) -> bool:
    employee = db.get(Employee, employee_id) if employee_id else None
    return bool(employee is not None and employee.is_demo)


async def _allowed(request: Request, db: Session) -> bool:
    path = request.url.path.rstrip("/")
    if request.method in ("GET", "HEAD"):
        # Подключение Google привязало бы чужой аккаунт к сотруднику
        return not path.startswith("/admin/calendars/connect")
    if path == "/admin/bookings/create":
        return True
    if match := BOOKING_ACTION.fullmatch(path):
        booking = db.get(Booking, int(match[1]))
        return booking is not None and (booking.is_demo or booking.employee.is_demo)
    if path in ("/admin/schedule/save", "/admin/schedule/exceptions/add"):
        return _is_demo_employee(db, _to_int((await request.form()).get("employee_id")))
    if match := EXCEPTION_DELETE.fullmatch(path):
        exception = db.get(ScheduleException, int(match[1]))
        return exception is not None and _is_demo_employee(db, exception.employee_id)
    if match := CLIENT_UPDATE.fullmatch(path):
        client = db.get(Client, int(match[1]))
        return client is not None and client.is_demo
    return False


async def _back(request: Request) -> str:
    if request.method == "POST":
        back = safe_back((await request.form()).get("back"))
        if back:
            return back
    referer = urlsplit(request.headers.get("referer", ""))
    return safe_back(referer.path + (f"?{referer.query}" if referer.query else "")) or "/admin/"


async def demo_guard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Зависимость на все роутеры админки: у обычных пользователей ничего не проверяет."""
    if not getattr(user, "is_demo", False) or await _allowed(request, db):
        return
    back = await _back(request)
    location = back + ("&" if "?" in back else "?") + "err=" + quote_plus(DENIED)
    raise HTTPException(status_code=303, headers={"Location": location})
