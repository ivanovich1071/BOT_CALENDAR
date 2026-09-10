"""Промпт разбора фразы клиента. Формулировки меняются здесь, логика — в app/ai/intent.py."""

import datetime as dt

WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Даты модель не вычисляет, а находит в готовом календаре: так «в пятницу»
# не превращается в прошлую пятницу или в несуществующее число
CALENDAR_DAYS = 14

# Длинные сообщения обрезаются: платим за разбор намерения, а не за сочинения
MAX_MESSAGE_CHARS = 500

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["book", "my_bookings", "info", "unknown"]},
        "service": {"type": ["string", "null"]},
        "employee": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "time_from": {"type": ["string", "null"], "description": "HH:MM"},
        "time_to": {"type": ["string", "null"], "description": "HH:MM"},
    },
    "required": ["action", "service", "employee", "date", "time_from", "time_to"],
    "additionalProperties": False,
}

SYSTEM = """Ты разбираешь сообщения клиентов бота онлайн-записи к специалисту.
Твоя единственная задача — понять намерение и вернуть JSON по схеме.
Ты не записываешь, не отменяешь и не отвечаешь клиенту.

action:
- "book" — клиент хочет записаться или спрашивает о свободном времени;
- "my_bookings" — спрашивает о своих записях, хочет перенести или отменить запись;
- "info" — спрашивает, как это работает и что умеет бот;
- "unknown" — всё остальное: приветствие без просьбы, посторонняя тема, бессмыслица.

service и employee — только название ТОЧНО из списков ниже или null. Ничего не придумывай.
Если клиент назвал услугу или специалиста, которых в списках нет, верни null.

date — только дата из календаря ниже в формате YYYY-MM-DD или null.
Не вычисляй даты сам: «завтра», «в пятницу», «14-го» найди в календаре.
Если дня нет в календаре или он не назван («на следующей неделе»), верни null.

time_from и time_to — окно времени в формате HH:MM или null:
- «утром» — 09:00 и 12:00; «днём», «после обеда» — 12:00 и 17:00; «вечером» — 17:00 и 21:00;
- точное время («в 15:00», «к трём часам дня») — одинаковые time_from и time_to;
- «после 14» — time_from 14:00, time_to null; «до 11» — time_from null, time_to 11:00."""


def calendar_lines(today: dt.date, days: int = CALENDAR_DAYS) -> str:
    labels = {0: "сегодня", 1: "завтра", 2: "послезавтра"}
    lines = []
    for offset in range(days):
        day = today + dt.timedelta(days=offset)
        label = f", {labels[offset]}" if offset in labels else ""
        lines.append(f"{day.isoformat()} — {WEEKDAYS_SHORT[day.weekday()]}{label}")
    return "\n".join(lines)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (нет)"


def build_messages(
    text: str, services: list[str], employees: list[str], today: dt.date
) -> list[dict]:
    context = (
        f"Услуги:\n{_bullets(services)}\n\n"
        f"Специалисты:\n{_bullets(employees)}\n\n"
        f"Календарь:\n{calendar_lines(today)}"
    )
    return [
        {"role": "system", "content": f"{SYSTEM}\n\n{context}"},
        {"role": "user", "content": text[:MAX_MESSAGE_CHARS]},
    ]
