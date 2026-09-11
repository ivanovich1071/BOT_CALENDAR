"""Ответ модели → HTML для Telegram.

Модель пишет лёгкий markdown (**жирный**, списки, иногда заголовки). Telegram
понимает только ограниченный HTML, поэтому текст экранируется целиком, а затем
разрешённые конструкции превращаются в теги — сломать разметку сообщения нельзя.
"""

import re
from html import escape

# Лимит Telegram — 4096 символов; оставляем запас на теги
MAX_CHARS = 3900


def to_telegram_html(text: str) -> str:
    html = escape(text.strip(), quote=False)
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html, flags=re.S)
    html = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", html)
    html = re.sub(r"(?m)^\s*[-*]\s+", "• ", html)
    html = re.sub(r"`([^`\n]+)`", r"\1", html)
    if len(html) > MAX_CHARS:
        cut = html.rfind("\n", 0, MAX_CHARS)
        html = html[: cut if cut > 0 else MAX_CHARS] + "\n…"
    # Обрезка или непарные ** могли оставить незакрытый тег — чиним
    if html.count("<b>") != html.count("</b>"):
        html = html.replace("<b>", "").replace("</b>", "")
    return html
