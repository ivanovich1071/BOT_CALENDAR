"""Системный промпт консультанта и перевод ответа модели в HTML Telegram."""

import datetime as dt

from app.ai.prompts.consultant import MAX_KNOWLEDGE_CHARS, build_system_prompt
from app.bot.formatting import to_telegram_html

TODAY = dt.date(2026, 9, 10)  # четверг

COMPANY = {
    "name": "ВайбМайнд",
    "assistant_name": "ИИ-консультант ВайбМайнд",
    "phone": "+375 29 7-200-700",
    "ai_rules": "Без давления.",
}
SERVICES = [
    {"id": 1, "name": "Встреча-знакомство", "duration": 30, "price_label": "бесплатно", "description": "Знакомство"},
    {"id": 2, "name": "Диагностика", "duration": 60, "price_label": "по договорённости", "description": None},
]
EMPLOYEES = [
    {"id": 10, "name": "Евгений", "specialization": "Разработчик", "bio": "Диагност", "service_ids": [2]},
    {"id": 11, "name": "Вероника", "specialization": None, "bio": None, "service_ids": []},
]


def _prompt(articles=None) -> str:
    return build_system_prompt(
        company=COMPANY,
        articles=articles if articles is not None else [{"title": "Цены", "body": "350 BYN за час"}],
        services=SERVICES,
        employees=EMPLOYEES,
        today=TODAY,
    )


def test_промпт_собирается_из_данных_компании():
    prompt = _prompt()
    assert "ИИ-консультант ВайбМайнд" in prompt and "«ВайбМайнд»" in prompt
    assert "ПРАВИЛА КОМПАНИИ\nБез давления." in prompt
    assert "Телефон: +375 29 7-200-700" in prompt
    assert "- id 1: Встреча-знакомство — 30 мин, бесплатно. Проводит: Вероника. Знакомство" in prompt
    assert "- id 2: Диагностика — 60 мин, по договорённости. Проводит: Евгений, Вероника." in prompt
    assert "- id 10: Евгений — Разработчик. Диагност Услуги: Диагностика." in prompt
    assert "2026-09-10 — чт, сегодня" in prompt and "2026-09-11 — пт, завтра" in prompt
    assert "### Цены\n350 BYN за час" in prompt
    assert "find_slots" in prompt and "propose_booking" in prompt


def test_дни_приёма_специалиста_в_промпте():
    employees = [{**EMPLOYEES[0], "weekdays": [0, 1, 2, 3, 4]}, EMPLOYEES[1]]
    prompt = build_system_prompt(company=COMPANY, articles=[], services=SERVICES, employees=employees, today=TODAY)
    assert "Обычно принимает: пн, вт, ср, чт, пт" in prompt
    assert "по договорённости»" in prompt  # правило о ценах услуг


def test_правила_из_живой_проверки():
    prompt = _prompt()
    # Исполнители меняются в админке, а база знаний — текст: верить списку услуг
    assert "верь «Услугам для записи»" in prompt
    # «на 17 можно?» без дня — не «сегодня», и дата всегда с днём недели
    assert "не подставляй «сегодня»" in prompt and "[Выбор кнопками: …]" in prompt


def test_длинная_база_знаний_обрезается():
    prompt = _prompt([{"title": "Много", "body": "а" * (MAX_KNOWLEDGE_CHARS + 500)}])
    assert "база знаний обрезана" in prompt
    assert prompt.count("а") < MAX_KNOWLEDGE_CHARS + 500


def test_markdown_превращается_в_html_telegram():
    assert to_telegram_html("**Цена** <b>x</b>\n- пункт\n## Итог") == (
        "<b>Цена</b> &lt;b&gt;x&lt;/b&gt;\n• пункт\n<b>Итог</b>"
    )


def test_непарные_звёздочки_не_ломают_разметку():
    assert "<b>" not in to_telegram_html("**жирный без конца")
