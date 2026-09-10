"""Живая проверка AI-разбора через настоящий OpenRouter.

    python scripts/ai_probe.py

Прогоняет несколько фраз разного вида и одну заведомо мусорную, печатает
разобранные намерения. Ключ берётся из .env и нигде не выводится.
Стоимость прогона — доли цента.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.intent import parse  # noqa: E402
from app.ai.prompts.intent import WEEKDAYS_SHORT  # noqa: E402
from app.config.network import prefer_ipv4  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.services.schedule_service import local_now  # noqa: E402

SERVICES = ["Консультация", "Стрижка мужская"]
EMPLOYEES = ["Иванов Тест", "Сергей Егоров"]

# (фраза, ожидаемое действие)
PHRASES = [
    ("хочу на консультацию завтра после обеда", "book"),
    ("запишите к Сергею на стрижку в пятницу на 11", "book"),
    ("есть что-нибудь в понедельник вечером?", "book"),
    ("а когда я записан?", "my_bookings"),
    ("сколько будет дважды два", "unknown"),
]


async def main() -> int:
    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY не заполнен")
        return 2
    if settings.prefer_ipv4:
        prefer_ipv4()

    today = local_now().date()
    print(f"Модель: {settings.openrouter_model}; сегодня {today} ({WEEKDAYS_SHORT[today.weekday()]})\n")

    correct = 0
    for phrase, expected in PHRASES:
        started = time.monotonic()
        intent = await parse(phrase, SERVICES, EMPLOYEES, today)
        took = time.monotonic() - started
        hit = intent.action == expected
        correct += hit
        print(f"{'✓' if hit else '✗'} «{phrase}»  [{took:.1f} с]")
        print(f"   {intent.model_dump(mode='json')}\n")

    print(f"Действие распознано верно: {correct} из {len(PHRASES)}")
    return 0 if correct == len(PHRASES) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
