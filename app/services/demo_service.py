"""Демо-доступ: общие логины для гостей и «Демо-специалист», которого не видит бот.

Гость (users.is_demo) смотрит всю админку, а меняет только демо-данные — записи,
которые создал сам, и всё у Демо-специалиста (защита — admin/demo.py). Созданное
гостем помечается is_demo. Ночной сброс удаляет помеченное и возвращает
Демо-специалиста и демо-логины к исходному виду. Записи и клиенты из бота он не трогает.
"""

import logging
import secrets
from datetime import time

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session

from app.config.security import hash_password
from app.models.booking import Booking
from app.models.client import Client
from app.models.employee import Employee
from app.models.enums import (
    EMPLOYEE,
    MANAGER,
    PERM_MANAGE_EMPLOYEES,
    PERM_MANAGE_SETTINGS,
    PERM_VIEW_AUDIT,
)
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.user import User
from app.services import booking_flow
from app.services.app_settings_service import DEMO, get_setting, set_setting
from app.services.audit_service import log_action

logger = logging.getLogger(__name__)

EMPLOYEE_NAME = "Демо-специалист"
EMPLOYEE_SPECIALIZATION = "Для пробы в админке"
EMPLOYEE_BIO = "Учебный специалист демо-доступа: в боте не показывается, ночью возвращается к исходному."
# Пн–Пт 10–18 с перерывом 13–14
WORKDAYS = range(5)
WORK_START, WORK_END, BREAK_START, BREAK_END = time(10), time(18), time(13), time(14)

ADMIN_LOGIN = "demo-admin"
EMPLOYEE_LOGIN = "demo-employee"
# Логин → (роль, дополнительные права, подпись). Демо-администратор — менеджер с правами
# на просмотр всех разделов: ролью admin его не делаем, чтобы он не считался администратором
ACCOUNTS = {
    ADMIN_LOGIN: (MANAGER, [PERM_MANAGE_EMPLOYEES, PERM_MANAGE_SETTINGS, PERM_VIEW_AUDIT], "Демо-администратор"),
    EMPLOYEE_LOGIN: (EMPLOYEE, [], "Демо-сотрудник"),
}
PASSWORD_WORDS = ("maple", "river", "amber", "cloud", "stone", "coral", "cedar", "comet")


class DemoError(Exception):
    pass


def _password() -> str:
    """Пароль для чтения вслух: его показывают гостям на странице входа."""
    return f"demo-{secrets.choice(PASSWORD_WORDS)}-{secrets.randbelow(9000) + 1000}"


def demo_employee(db: Session) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.is_demo.is_(True)).order_by(Employee.id))


def enabled(db: Session) -> bool:
    return bool(get_setting(db, DEMO).get("enabled"))


def login_hints(db: Session) -> list[dict]:
    """Логины для страницы входа — только если администратор включил показ."""
    config = get_setting(db, DEMO)
    if not (config.get("enabled") and config.get("show_on_login")):
        return []
    active = set(db.scalars(select(User.login).where(User.is_demo.is_(True), User.is_active.is_(True))))
    return [item for item in config.get("logins") or [] if item.get("login") in active]


def _restore_employee(db: Session, employee: Employee) -> None:
    employee.name = EMPLOYEE_NAME
    employee.specialization = EMPLOYEE_SPECIALIZATION
    employee.bio = EMPLOYEE_BIO
    employee.phone = None
    employee.is_active = True
    employee.is_demo = True
    employee.archived_at = None
    employee.telegram_user_id = None
    employee.default_calendar_id = None
    employee.services = []  # без привязок — оказывает все услуги
    db.flush()
    db.execute(delete(Schedule).where(Schedule.employee_id == employee.id))
    db.execute(delete(ScheduleException).where(ScheduleException.employee_id == employee.id))
    for weekday in WORKDAYS:
        db.add(Schedule(employee_id=employee.id, weekday=weekday, start_time=WORK_START, end_time=WORK_END,
                        break_start=BREAK_START, break_end=BREAK_END, is_active=True))
    db.commit()
    db.expire_all()


def _restore_user(user: User) -> None:
    role, permissions, _label = ACCOUNTS[user.login]
    user.role = role
    user.permissions = list(permissions)
    user.is_active = True
    user.is_demo = True


def setup(db: Session, *, actor: str = "cli") -> list[dict]:
    """Заводит Демо-специалиста и демо-логины. Повторный запуск ничего не дублирует
    и пароли не меняет. Возвращает логины с паролями."""
    employee = demo_employee(db)
    if employee is None:
        employee = Employee(name=EMPLOYEE_NAME, is_demo=True)
        db.add(employee)
        db.commit()
    _restore_employee(db, employee)

    known = {item.get("login"): item for item in get_setting(db, DEMO).get("logins") or []}
    logins = []
    for login, (_role, _permissions, label) in ACCOUNTS.items():
        user = db.scalar(select(User).where(User.login == login))
        password = (known.get(login) or {}).get("password")
        if user is None:
            password = _password()
            user = User(login=login, password_hash=hash_password(password))
            db.add(user)
        elif not user.is_demo:
            raise DemoError(f"Логин {login} уже занят обычным пользователем")
        elif not password:
            password = _password()
            user.password_hash = hash_password(password)
        _restore_user(user)
        logins.append({"login": login, "password": password, "label": label})
    db.commit()

    staff = db.scalar(select(User).where(User.login == EMPLOYEE_LOGIN))
    employee = demo_employee(db)
    if employee.user_id != staff.id:
        employee.user_id = staff.id
        db.commit()

    config = get_setting(db, DEMO)
    set_setting(db, DEMO, {**config, "enabled": True, "logins": logins})
    log_action(db, actor=actor, action="demo.setup", entity_type="demo", details={"logins": list(ACCOUNTS)})
    return logins


def reset(db: Session, *, actor: str = "system") -> dict:
    """Возвращает демо к исходному: удаляет пробы гостей, восстанавливает Демо-специалиста.

    Записи удаляются через booking_flow — у активных события уходят из Google.
    """
    employee = demo_employee(db)
    condition = Booking.is_demo.is_(True)
    if employee is not None:
        condition = or_(condition, Booking.employee_id == employee.id)
    booking_ids = list(db.scalars(select(Booking.id).where(condition)))
    for booking_id in booking_ids:
        booking_flow.delete(db, booking_id, actor=actor)

    removed_clients = 0
    for client in db.scalars(select(Client).where(Client.is_demo.is_(True))).all():
        if not db.scalar(select(exists().where(Booking.client_id == client.id))):
            db.delete(client)  # реплики диалогов уходят каскадом
            removed_clients += 1
    db.commit()

    if employee is not None:
        _restore_employee(db, employee)
    for user in db.scalars(select(User).where(User.is_demo.is_(True), User.login.in_(list(ACCOUNTS)))).all():
        _restore_user(user)
    db.commit()

    result = {"bookings": len(booking_ids), "clients": removed_clients}
    log_action(db, actor=actor, action="demo.reset", entity_type="demo", details=result)
    return result
