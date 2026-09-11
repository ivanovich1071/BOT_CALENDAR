"""Инструменты ИИ-консультанта.

Модель вызывает их по имени; здесь — описания для модели и исполнение поверх
app/bot/services.py, где уже проверяется, что клиент трогает только своё.
Ни один инструмент не записывает: propose_booking только готовит карточку,
запись создаёт нажатие кнопки клиентом.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from app.ai.prompts.consultant import WEEKDAYS_SHORT
from app.ai.resolve import filter_times
from app.bot import services

logger = logging.getLogger(__name__)

MAX_SLOTS = 12
SLOTS_PER_EMPLOYEE_DAY = 3
MAX_RANGE_DAYS = 7

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_slots",
            "description": (
                "Свободное время на услугу. Возвращает до 12 вариантов: специалист, дата, время. "
                "Период — не больше 7 дней от date_from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "integer", "description": "id услуги из списка услуг"},
                    "employee_id": {"type": ["integer", "null"], "description": "id специалиста или null — любой"},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD из календаря"},
                    "date_to": {"type": ["string", "null"], "description": "YYYY-MM-DD или null — один день"},
                    "time_from": {"type": ["string", "null"], "description": "HH:MM или null"},
                    "time_to": {"type": ["string", "null"], "description": "HH:MM или null"},
                },
                "required": ["service_id", "date_from"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_booking",
            "description": (
                "Показать клиенту карточку записи с кнопкой «Записаться». Время — только из find_slots. "
                "Сам не записывает."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "integer"},
                    "employee_id": {"type": "integer"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM"},
                    "summary": {
                        "type": "string",
                        "description": "С чем клиент придёт: 1–2 предложения для специалиста, без имени и телефона",
                    },
                },
                "required": ["service_id", "employee_id", "date", "time", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_bookings",
            "description": "Активные записи клиента. Бот покажет их с кнопками «Перенести» и «Отменить».",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass
class Proposal:
    """Предложенная запись — ждёт нажатия «Записаться». Хранится в состоянии диалога."""

    service_id: int
    employee_id: int
    service: str
    employee: str
    day: str  # YYYY-MM-DD: состояние aiogram сериализуется в JSON
    slot: str
    end: str
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        return cls(**data)


@dataclass
class ToolContext:
    client_id: int
    proposal: Proposal | None = None
    show_bookings: bool = False


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _time(value) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(str(value)[:5])
    except ValueError:
        return None


def _service(db: Session, service_id) -> dict | None:
    try:
        wanted = int(service_id)
    except (TypeError, ValueError):
        return None
    return next((s for s in services.active_services(db) if s["id"] == wanted), None)


def find_slots(db: Session, ctx: ToolContext, args: dict) -> dict:
    service = _service(db, args.get("service_id"))
    if service is None:
        return {"error": "Нет такой услуги — возьми id из списка услуг."}
    employees = services.employees_with_schedule(db, service["id"])
    if args.get("employee_id"):
        employees = [e for e in employees if e["id"] == args["employee_id"]]
        if not employees:
            return {"error": "Этот специалист не проводит эту услугу — выбери из списка услуг."}

    today, last = services.horizon()
    start = max(_date(args.get("date_from")) or today, today)
    end = _date(args.get("date_to")) or start
    end = min(max(end, start), start + timedelta(days=MAX_RANGE_DAYS - 1), last)
    time_from, time_to = _time(args.get("time_from")), _time(args.get("time_to"))

    slots: list[dict] = []
    day = start
    while day <= end and len(slots) < MAX_SLOTS:
        for e in employees:
            # Сначала расписание — Google спрашиваем только про рабочие дни
            if day not in services.open_days(db, e["id"], day, day):
                continue
            times = filter_times(services.free_times(db, e["id"], service["id"], day), time_from, time_to)
            for slot in times[: min(SLOTS_PER_EMPLOYEE_DAY, MAX_SLOTS - len(slots))]:
                slots.append(
                    {
                        "employee_id": e["id"],
                        "employee": e["name"],
                        "date": day.isoformat(),
                        "weekday": WEEKDAYS_SHORT[day.weekday()],
                        "time": slot,
                    }
                )
        day += timedelta(days=1)

    result = {
        "service_id": service["id"],
        "service": service["name"],
        "period": f"{start.isoformat()} — {end.isoformat()}",
        "slots": slots,
    }
    if not slots:
        result["note"] = "В этом периоде свободного времени нет — предложи другой период или специалиста."
    else:
        result["next_step"] = (
            "Если клиент назвал время или просил ближайшее, любое, первое свободное — сразу вызови "
            "propose_booking с подходящим слотом, не переспрашивая. Иначе назови до 3 вариантов и спроси."
        )
    return result


def propose_booking(db: Session, ctx: ToolContext, args: dict) -> dict:
    service = _service(db, args.get("service_id"))
    if service is None:
        return {"ok": False, "error": "Нет такой услуги."}
    employee = next(
        (e for e in services.employees_with_schedule(db, service["id"]) if e["id"] == args.get("employee_id")),
        None,
    )
    if employee is None:
        return {"ok": False, "error": "Этот специалист не проводит эту услугу."}
    day = _date(args.get("date"))
    today, last = services.horizon()
    if day is None or not today <= day <= last:
        return {"ok": False, "error": "Дата вне периода записи."}
    slot = str(args.get("time") or "")[:5]
    if slot not in services.free_times(db, employee["id"], service["id"], day):
        return {"ok": False, "error": "Это время недоступно. Вызови find_slots и предложи другое."}

    end = services.parse_slot(day, slot) + timedelta(minutes=service["duration"])
    ctx.proposal = Proposal(
        service_id=service["id"],
        employee_id=employee["id"],
        service=service["name"],
        employee=employee["name"],
        day=day.isoformat(),
        slot=slot,
        end=end.strftime("%H:%M"),
        summary=str(args.get("summary") or "").strip()[:500],
    )
    return {
        "ok": True,
        "note": "Под твоим ответом клиент увидит карточку с кнопкой «Записаться». Коротко подтверди выбор "
        "и попроси нажать кнопку. Не пиши, что запись уже создана.",
    }


def my_bookings(db: Session, ctx: ToolContext, args: dict) -> dict:
    cards = services.my_bookings(db, ctx.client_id)
    ctx.show_bookings = True
    return {
        "bookings": [
            {"service": c["service"], "employee": c["employee"], "date": c["date"].isoformat(), "start": c["start"]}
            for c in cards
        ],
        "note": "Бот покажет эти записи с кнопками «Перенести» и «Отменить». Если записей нет — предложи записаться.",
    }


HANDLERS = {"find_slots": find_slots, "propose_booking": propose_booking, "my_bookings": my_bookings}


def run_tool(db: Session, ctx: ToolContext, name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"Инструмента {name} нет."}
    try:
        return handler(db, ctx, args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 — сбой инструмента не должен ронять диалог
        logger.exception("Инструмент %s упал", name)
        db.rollback()
        return {"error": "Не удалось выполнить — предложи клиенту выбрать время кнопкой «Записаться»."}
