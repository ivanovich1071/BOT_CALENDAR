"""Единый Jinja2-окружение для админки."""

from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config.settings import get_settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _to_local(dt):
    if dt is None:
        return None
    return dt.astimezone(ZoneInfo(get_settings().timezone))


templates.env.filters["localtime"] = lambda dt: _to_local(dt).strftime("%H:%M") if dt else "—"
templates.env.filters["localdate"] = lambda dt: _to_local(dt).strftime("%d.%m.%Y") if dt else "—"
templates.env.filters["localdt"] = lambda dt: (
    _to_local(dt).strftime("%d.%m.%Y %H:%M") if dt else "—"
)


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 запись», «3 записи», «5 записей»."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


templates.env.filters["plural"] = plural


def render(request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = {
        "request": request,
        "flash_ok": request.query_params.get("ok"),
        "flash_err": request.query_params.get("err"),
        **(context or {}),
    }
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
