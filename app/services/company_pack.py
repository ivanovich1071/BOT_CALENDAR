"""Пакет компании: профиль, база знаний, услуги, сотрудники и расписания одним JSON.

Нужен, чтобы показать демо под другую компанию: загрузил её пакет — бот и ИИ
заговорили от её имени; вернул свой — всё как было. Записи, клиенты, логины и
Google-подключения пакет не трогает: услуги и сотрудники сопоставляются по имени,
а всё, чего в пакете нет, уходит в архив.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.enums import EXCEPTION_KINDS
from app.models.knowledge_article import KnowledgeArticle
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.service import Service
from app.services.app_settings_service import COMPANY, COMPANY_FIELDS, get_setting, set_setting
from app.services.audit_service import log_action

PACK_FORMAT = "bot-calendar-company-pack"
PACK_VERSION = 1


class PackError(ValueError):
    """Пакет не прошёл проверку — текст ошибки показывается пользователю."""


@dataclass
class ImportPlan:
    services_added: list[str] = field(default_factory=list)
    services_updated: list[str] = field(default_factory=list)
    services_archived: list[str] = field(default_factory=list)
    employees_added: list[str] = field(default_factory=list)
    employees_updated: list[str] = field(default_factory=list)
    employees_archived: list[str] = field(default_factory=list)
    articles: int = 0
    company: str = ""

    def lines(self) -> list[str]:
        def names(items: list[str]) -> str:
            return ", ".join(items) if items else "—"

        return [
            f"Компания: {self.company or '—'}",
            f"Статей базы знаний: {self.articles} (текущие заменятся)",
            f"Услуги — добавятся: {names(self.services_added)}",
            f"Услуги — обновятся: {names(self.services_updated)}",
            f"Услуги — в архив: {names(self.services_archived)}",
            f"Сотрудники — добавятся: {names(self.employees_added)}",
            f"Сотрудники — обновятся: {names(self.employees_updated)}",
            f"Сотрудники — в архив: {names(self.employees_archived)}",
        ]


def _key(name: str) -> str:
    return name.strip().casefold().replace("ё", "е")


def _hhmm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


# ==== Экспорт ====

def export_pack(db: Session) -> dict:
    services = db.scalars(
        select(Service).where(Service.archived_at.is_(None)).order_by(Service.sort_order, Service.name)
    ).all()
    employees = db.scalars(
        select(Employee).where(Employee.archived_at.is_(None)).order_by(Employee.name)
    ).all()
    articles = db.scalars(
        select(KnowledgeArticle).order_by(KnowledgeArticle.sort_order, KnowledgeArticle.id)
    ).all()
    company = get_setting(db, COMPANY)
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "company": {f: company.get(f, "") for f in COMPANY_FIELDS},
        "knowledge": [
            {"title": a.title, "body": a.body, "sort_order": a.sort_order, "is_active": a.is_active}
            for a in articles
        ],
        "services": [
            {
                "name": s.name,
                "description": s.description,
                "duration_minutes": s.duration_minutes,
                "price": float(s.price) if s.price is not None else None,
                "sort_order": s.sort_order,
                "is_active": s.is_active,
            }
            for s in services
        ],
        "employees": [
            {
                "name": e.name,
                "phone": e.phone,
                "specialization": e.specialization,
                "bio": e.bio,
                "is_active": e.is_active,
                "services": sorted(s.name for s in e.services if s.archived_at is None),
                "schedule": [
                    {
                        "weekday": r.weekday,
                        "start": _hhmm(r.start_time),
                        "end": _hhmm(r.end_time),
                        "break_start": _hhmm(r.break_start),
                        "break_end": _hhmm(r.break_end),
                    }
                    for r in sorted(e.schedules, key=lambda r: r.weekday)
                    if r.is_active
                ],
                "exceptions": [
                    {
                        "date_from": x.date_from.isoformat(),
                        "date_to": x.date_to.isoformat(),
                        "kind": x.kind,
                        "start": _hhmm(x.start_time),
                        "end": _hhmm(x.end_time),
                        "note": x.note,
                    }
                    for x in sorted(e.exceptions, key=lambda x: x.date_from)
                ],
            }
            for e in employees
        ],
    }


# ==== Проверка ====

def _text(value, where: str, *, required: bool = False, limit: int | None = None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PackError(f"{where}: поле обязательно")
        return None
    if not isinstance(value, str):
        raise PackError(f"{where}: ожидается текст")
    value = value.strip()
    if limit and len(value) > limit:
        raise PackError(f"{where}: длиннее {limit} символов")
    return value


def _time(value, where: str) -> time | None:
    if value in (None, ""):
        return None
    try:
        return time.fromisoformat(str(value))
    except ValueError as e:
        raise PackError(f"{where}: время в формате ЧЧ:ММ") from e


def _date(value, where: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise PackError(f"{where}: дата в формате ГГГГ-ММ-ДД") from e


def _unique(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if _key(name) in seen:
            raise PackError(f"{what} «{name}» встречается дважды")
        seen.add(_key(name))


def validate_pack(data) -> dict:
    """Проверяет пакет и приводит значения к типам. Бросает PackError с понятным текстом."""
    if not isinstance(data, dict) or data.get("format") != PACK_FORMAT:
        raise PackError("Это не пакет компании (нет поля format)")
    if data.get("version") != PACK_VERSION:
        raise PackError(f"Неподдерживаемая версия пакета: {data.get('version')}")

    raw_company = data.get("company") or {}
    if not isinstance(raw_company, dict):
        raise PackError("company: ожидается объект")
    company = {f: _text(raw_company.get(f), f"company.{f}") or "" for f in COMPANY_FIELDS}
    if not company["name"]:
        raise PackError("company.name: укажите название компании")

    knowledge = []
    for i, a in enumerate(data.get("knowledge") or [], 1):
        where = f"knowledge[{i}]"
        knowledge.append(
            {
                "title": _text(a.get("title"), f"{where}.title", required=True, limit=200),
                "body": _text(a.get("body"), f"{where}.body") or "",
                "sort_order": int(a.get("sort_order") or i),
                "is_active": bool(a.get("is_active", True)),
            }
        )

    services = []
    for i, s in enumerate(data.get("services") or [], 1):
        where = f"services[{i}]"
        duration = s.get("duration_minutes")
        if not isinstance(duration, int) or not 5 <= duration <= 600:
            raise PackError(f"{where}.duration_minutes: целое число минут от 5 до 600")
        price = s.get("price")
        if price is not None:
            try:
                price = Decimal(str(price))
            except InvalidOperation as e:
                raise PackError(f"{where}.price: число или null") from e
            if price < 0:
                raise PackError(f"{where}.price: не может быть отрицательной")
        services.append(
            {
                "name": _text(s.get("name"), f"{where}.name", required=True, limit=120),
                "description": _text(s.get("description"), f"{where}.description"),
                "duration_minutes": duration,
                "price": price,
                "sort_order": int(s.get("sort_order") or i),
                "is_active": bool(s.get("is_active", True)),
            }
        )
    _unique([s["name"] for s in services], "Услуга")
    service_keys = {_key(s["name"]) for s in services}

    employees = []
    for i, e in enumerate(data.get("employees") or [], 1):
        where = f"employees[{i}]"
        name = _text(e.get("name"), f"{where}.name", required=True, limit=120)
        linked = []
        for service_name in e.get("services") or []:
            if _key(str(service_name)) not in service_keys:
                raise PackError(f"{where}.services: услуги «{service_name}» нет в пакете")
            linked.append(str(service_name))

        schedule = []
        for row in e.get("schedule") or []:
            weekday = row.get("weekday")
            if not isinstance(weekday, int) or not 0 <= weekday <= 6:
                raise PackError(f"{where}.schedule: weekday от 0 (пн) до 6 (вс)")
            start, end = _time(row.get("start"), f"{where}.schedule"), _time(row.get("end"), f"{where}.schedule")
            if not (start and end and end > start):
                raise PackError(f"{where}.schedule: конец рабочего дня позже начала")
            schedule.append(
                {
                    "weekday": weekday,
                    "start": start,
                    "end": end,
                    "break_start": _time(row.get("break_start"), f"{where}.schedule"),
                    "break_end": _time(row.get("break_end"), f"{where}.schedule"),
                }
            )
        if len({r["weekday"] for r in schedule}) != len(schedule):
            raise PackError(f"{where}.schedule: один день недели указан дважды")

        exceptions = []
        for row in e.get("exceptions") or []:
            kind = row.get("kind")
            if kind not in EXCEPTION_KINDS:
                raise PackError(f"{where}.exceptions: kind — одно из {', '.join(EXCEPTION_KINDS)}")
            date_from = _date(row.get("date_from"), f"{where}.exceptions")
            date_to = _date(row.get("date_to") or row.get("date_from"), f"{where}.exceptions")
            if date_to < date_from:
                raise PackError(f"{where}.exceptions: date_to раньше date_from")
            exceptions.append(
                {
                    "date_from": date_from,
                    "date_to": date_to,
                    "kind": kind,
                    "start": _time(row.get("start"), f"{where}.exceptions"),
                    "end": _time(row.get("end"), f"{where}.exceptions"),
                    "note": _text(row.get("note"), f"{where}.exceptions.note", limit=200),
                }
            )

        employees.append(
            {
                "name": name,
                "phone": _text(e.get("phone"), f"{where}.phone", limit=32),
                "specialization": _text(e.get("specialization"), f"{where}.specialization", limit=120),
                "bio": _text(e.get("bio"), f"{where}.bio"),
                "is_active": bool(e.get("is_active", True)),
                "services": linked,
                "schedule": schedule,
                "exceptions": exceptions,
            }
        )
    _unique([e["name"] for e in employees], "Сотрудник")

    return {"company": company, "knowledge": knowledge, "services": services, "employees": employees}


# ==== Импорт ====

def _by_name(rows) -> dict:
    return {_key(r.name): r for r in rows}


def plan_import(db: Session, pack: dict) -> ImportPlan:
    """Что изменится при импорте — показывается до применения."""
    plan = ImportPlan(company=pack["company"]["name"], articles=len(pack["knowledge"]))
    services = _by_name(db.scalars(select(Service)).all())
    employees = _by_name(db.scalars(select(Employee)).all())
    pack_services = {_key(s["name"]) for s in pack["services"]}
    pack_employees = {_key(e["name"]) for e in pack["employees"]}

    for s in pack["services"]:
        (plan.services_updated if _key(s["name"]) in services else plan.services_added).append(s["name"])
    for e in pack["employees"]:
        (plan.employees_updated if _key(e["name"]) in employees else plan.employees_added).append(e["name"])
    plan.services_archived = [
        s.name for k, s in services.items() if k not in pack_services and s.archived_at is None
    ]
    plan.employees_archived = [
        e.name for k, e in employees.items() if k not in pack_employees and e.archived_at is None
    ]
    return plan


def import_knowledge(db: Session, pack: dict, *, actor: str) -> int:
    """Заменяет только базу знаний из пакета. Услуги, сотрудники и профиль, поправленные
    в админке, не трогает — в отличие от import_pack. Возвращает число статей."""
    try:
        db.execute(delete(KnowledgeArticle))
        for a in pack["knowledge"]:
            db.add(KnowledgeArticle(**a))
        db.commit()
    except Exception:
        db.rollback()
        raise
    log_action(
        db, actor=actor, action="knowledge.import", entity_type="knowledge",
        details={"company": pack["company"]["name"], "articles": len(pack["knowledge"])},
    )
    return len(pack["knowledge"])


def import_pack(db: Session, pack: dict, *, actor: str, user_id: int | None = None) -> ImportPlan:
    """Применяет проверенный пакет (результат validate_pack) одной транзакцией."""
    plan = plan_import(db, pack)
    now = datetime.now(timezone.utc)
    try:
        services = _by_name(db.scalars(select(Service)).all())
        for s in pack["services"]:
            row = services.get(_key(s["name"]))
            if row is None:
                row = Service(name=s["name"])
                db.add(row)
                services[_key(s["name"])] = row
            row.name = s["name"]
            row.description = s["description"]
            row.duration_minutes = s["duration_minutes"]
            row.price = s["price"]
            row.sort_order = s["sort_order"]
            row.is_active = s["is_active"]
            row.archived_at = None
        pack_services = {_key(s["name"]) for s in pack["services"]}
        for k, row in services.items():
            if k not in pack_services and row.archived_at is None:
                row.archived_at = now

        employees = _by_name(db.scalars(select(Employee)).all())
        for e in pack["employees"]:
            row = employees.get(_key(e["name"]))
            if row is None:
                row = Employee(name=e["name"])
                db.add(row)
                employees[_key(e["name"])] = row
            row.name = e["name"]
            if e["phone"]:  # в пакете без телефона — оставляем тот, что завели в админке
                row.phone = e["phone"]
            row.specialization = e["specialization"]
            row.bio = e["bio"]
            row.is_active = e["is_active"]
            row.archived_at = None
            row.services = [services[_key(name)] for name in e["services"]]
            db.flush()

            db.execute(delete(Schedule).where(Schedule.employee_id == row.id))
            db.execute(delete(ScheduleException).where(ScheduleException.employee_id == row.id))
            for r in e["schedule"]:
                db.add(
                    Schedule(
                        employee_id=row.id,
                        weekday=r["weekday"],
                        start_time=r["start"],
                        end_time=r["end"],
                        break_start=r["break_start"],
                        break_end=r["break_end"],
                        is_active=True,
                    )
                )
            for x in e["exceptions"]:
                db.add(
                    ScheduleException(
                        employee_id=row.id,
                        date_from=x["date_from"],
                        date_to=x["date_to"],
                        kind=x["kind"],
                        start_time=x["start"],
                        end_time=x["end"],
                        note=x["note"],
                    )
                )
        pack_employees = {_key(e["name"]) for e in pack["employees"]}
        for k, row in employees.items():
            if k not in pack_employees and row.archived_at is None:
                row.archived_at = now

        db.execute(delete(KnowledgeArticle))
        for a in pack["knowledge"]:
            db.add(KnowledgeArticle(**a))
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Расписания удалялись массовым DELETE — сбрасываем закэшированные коллекции сессии
    db.expire_all()

    set_setting(db, COMPANY, pack["company"])
    log_action(
        db,
        actor=actor,
        action="company.import",
        entity_type="company",
        entity_id=None,
        user_id=user_id,
        details={
            "company": plan.company,
            "services": len(pack["services"]),
            "employees": len(pack["employees"]),
            "articles": plan.articles,
            "archived": plan.services_archived + plan.employees_archived,
        },
    )
    return plan
