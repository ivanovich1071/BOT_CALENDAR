"""Компания: профиль, настройки ИИ-консультанта, пакет компании, профиль бота в Telegram."""

import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.diff import changed_fields
from admin.flash import redirect
from admin.templating import render
from app.ai.prompts.consultant import MAX_KNOWLEDGE_CHARS
from app.api.dependencies import require_permission
from app.bot.profile import BotProfileError, apply_bot_profile
from app.config.settings import get_settings
from app.db.database import get_db
from app.models.knowledge_article import KnowledgeArticle
from app.models.user import User
from app.services import company_pack
from app.services.app_settings_service import AI, COMPANY, COMPANY_FIELDS, get_setting, set_setting
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/company")

FIELD_LABELS = {
    "name": "Название",
    "tagline": "Слоган",
    "description": "Описание",
    "phone": "Телефон",
    "telegram": "Telegram",
    "website": "Сайт",
    "email": "Email",
    "currency": "Валюта цен",
    "greeting": "Приветствие бота",
    "assistant_name": "Имя ИИ-консультанта",
    "ai_rules": "Правила для ИИ",
}
FIELD_LIMITS = {"name": 120, "description": 2000, "greeting": 1000, "ai_rules": 4000}
DEFAULT_FIELD_LIMIT = 200
MAX_PACK_BYTES = 2_000_000


def _int(value: str, label: str, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        number = None
    if number is None or not low <= number <= high:
        raise ValueError(f"{label}: целое число от {low} до {high}")
    return number


def parse_ai_settings(model: str, temperature: str, hourly_limit: str, history_messages: str, history_hours: str) -> dict:
    model = model.strip()
    if len(model) > 100 or " " in model:
        raise ValueError("Модель: идентификатор OpenRouter, например qwen/qwen3-235b-a22b-2507")
    temp = None
    if temperature.strip():
        try:
            temp = float(temperature.strip().replace(",", "."))
        except ValueError:
            temp = -1
        if not 0 <= temp <= 1.5:
            raise ValueError("Температура: число от 0 до 1.5")
    return {
        "model": model,
        "temperature": temp,
        "hourly_limit": _int(hourly_limit, "Сообщений в час на клиента", 1, 500),
        "history_messages": _int(history_messages, "Реплик в памяти", 0, 40),
        "history_hours": _int(history_hours, "Память разговора, часов", 1, 72),
    }


@router.get("")
async def company_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    s = get_settings()
    articles = db.scalars(select(KnowledgeArticle)).all()
    return render(
        request,
        "company/index.html",
        {
            "user": user,
            "nav": "company",
            "company": get_setting(db, COMPANY),
            "labels": FIELD_LABELS,
            "ai": get_setting(db, AI),
            "env_model": s.openrouter_model,
            "env_temperature": s.openrouter_temperature,
            "articles_count": len(articles),
            "kb_chars": sum(len(a.title) + len(a.body) for a in articles if a.is_active),
            "kb_limit": MAX_KNOWLEDGE_CHARS,
            "bot_ready": bool(s.bot_token),
            "page_title": "Компания",
        },
    )


@router.post("/profile")
async def save_profile(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    form = await request.form()
    value = {f: str(form.get(f) or "").strip() for f in COMPANY_FIELDS}
    if not value["name"]:
        return redirect("/admin/company", err="Укажите название компании")
    for f, text in value.items():
        limit = FIELD_LIMITS.get(f, DEFAULT_FIELD_LIMIT)
        if len(text) > limit:
            return redirect("/admin/company", err=f"{FIELD_LABELS[f]}: не длиннее {limit} символов")
    before = get_setting(db, COMPANY)
    set_setting(db, COMPANY, value)
    log_action(
        db, actor=user.login, action="company.update", entity_type="app_setting", entity_id=COMPANY,
        details=changed_fields(before, value), user_id=user.id,
    )
    return redirect("/admin/company", ok="Профиль компании сохранён")


@router.post("/ai")
async def save_ai(
    model: str = Form(""),
    temperature: str = Form(""),
    hourly_limit: str = Form("30"),
    history_messages: str = Form("12"),
    history_hours: str = Form("3"),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    try:
        value = parse_ai_settings(model, temperature, hourly_limit, history_messages, history_hours)
    except ValueError as exc:
        return redirect("/admin/company", err=str(exc))
    before = get_setting(db, AI)
    set_setting(db, AI, value)
    log_action(
        db, actor=user.login, action="settings.ai", entity_type="app_setting", entity_id=AI,
        details=changed_fields(before, value), user_id=user.id,
    )
    return redirect("/admin/company", ok="Настройки ИИ-консультанта сохранены")


@router.get("/export")
async def export_pack(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    data = json.dumps(company_pack.export_pack(db), ensure_ascii=False, indent=2)
    log_action(db, actor=user.login, action="company.export", entity_type="company", user_id=user.id)
    return Response(
        content=data.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="company-pack.json"'},
    )


def _read_pack(raw: bytes | str) -> tuple[dict, dict]:
    """(исходный JSON, проверенный пакет). Бросает ValueError с понятным текстом."""
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Файл — не JSON") from exc
    try:
        return data, company_pack.validate_pack(data)
    except company_pack.PackError as exc:
        raise ValueError(f"Пакет не прошёл проверку: {exc}") from exc


@router.post("/import")
async def import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    raw = await file.read(MAX_PACK_BYTES + 1)
    if len(raw) > MAX_PACK_BYTES:
        return redirect("/admin/company", err="Файл больше 2 МБ — это не пакет компании")
    try:
        data, pack = _read_pack(raw)
    except ValueError as exc:
        return redirect("/admin/company", err=str(exc))
    plan = company_pack.plan_import(db, pack)
    return render(
        request,
        "company/import_preview.html",
        {
            "user": user,
            "nav": "company",
            "plan_lines": plan.lines(),
            "raw": json.dumps(data, ensure_ascii=False),
            "page_title": "Загрузка пакета компании",
        },
    )


@router.post("/import/apply")
async def import_apply(
    pack_json: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    try:
        _data, pack = _read_pack(pack_json)
    except ValueError as exc:
        return redirect("/admin/company", err=str(exc))
    plan = company_pack.import_pack(db, pack, actor=user.login, user_id=user.id)
    return redirect("/admin/company", ok=f"Загружена компания «{plan.company}»")


@router.post("/telegram")
async def apply_telegram(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    token = get_settings().bot_token
    if not token:
        return redirect("/admin/company", err="BOT_TOKEN не заполнен в .env")
    company = get_setting(db, COMPANY)
    try:
        await apply_bot_profile(token, company)
    except BotProfileError as exc:
        return redirect("/admin/company", err=str(exc))
    log_action(
        db, actor=user.login, action="company.telegram", entity_type="company",
        details={"name": company.get("name")}, user_id=user.id,
    )
    return redirect("/admin/company", ok="Имя и описание бота обновлены в Telegram")
