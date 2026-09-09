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


def render(request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = {
        "request": request,
        "flash_ok": request.query_params.get("ok"),
        "flash_err": request.query_params.get("err"),
        **(context or {}),
    }
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
