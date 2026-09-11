from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from admin.flash import redirect
from admin.templating import render
from app.api.dependencies import require_permission
from app.config.settings import get_settings
from app.db.database import get_db
from app.models.user import User
from app.services import reminder_service
from admin.diff import changed_fields
from app.services.app_settings_service import BOOKING, GOOGLE_SYNC, REMINDERS, get_setting, set_setting
from app.services.audit_service import log_action

# (поле формы, ключ настройки, подпись, минимум, максимум)
BOOKING_FIELDS = (
    ("slot_step", "slot_step_minutes", "Шаг сетки, минут", 5, 240),
    ("horizon_days", "horizon_days", "Запись вперёд, дней", 1, 365),
    ("min_lead", "min_lead_minutes", "Минимум до начала, минут", 0, 10080),
)
SYNC_LIMITS = (1, 120)

router = APIRouter(prefix="/admin/settings")


@router.get("")
async def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    s = get_settings()
    integrations = {
        "telegram": {
            "ok": bool(s.bot_token),
            "label": "Telegram Bot",
            "hint": "Токен из @BotFather",
            "detail": "настроен" if s.bot_token else "BOT_TOKEN не заполнен",
        },
        "openrouter": {
            "ok": bool(s.openrouter_api_key),
            "label": "OpenRouter (AI)",
            "hint": "Ключ и модель",
            "detail": s.openrouter_model if s.openrouter_api_key else "OPENROUTER_API_KEY не заполнен",
        },
        "google": {
            "ok": bool(s.google_client_id and s.google_client_secret),
            "label": "Google Calendar (OAuth)",
            "hint": "Client ID / Secret для подключения календарей сотрудников",
            "detail": "настроен" if s.google_client_id and s.google_client_secret else "GOOGLE_CLIENT_ID/SECRET не заполнены",
        },
    }
    reminders = get_setting(db, REMINDERS)
    return render(
        request,
        "settings/index.html",
        {
            "user": user,
            "nav": "settings",
            "integrations": integrations,
            "app_env": s.app_env,
            "timezone": s.timezone,
            "reminders_enabled": bool(reminders.get("enabled", True)),
            "reminder_hours": ", ".join(str(h) for h in reminders.get("hours_before", [])),
            "booking": get_setting(db, BOOKING),
            "booking_fields": BOOKING_FIELDS,
            "sync_minutes": get_setting(db, GOOGLE_SYNC).get("interval_minutes", 10),
            "page_title": "Настройки",
        },
    )


@router.post("/booking")
async def save_booking_settings(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    form = await request.form()
    value = {}
    try:
        for field, key, label, low, high in BOOKING_FIELDS:
            number = int(str(form.get(field) or "").strip())
            if not low <= number <= high:
                raise ValueError(f"{label}: от {low} до {high}")
            value[key] = number
        sync = int(str(form.get("sync_minutes") or "").strip())
        if not SYNC_LIMITS[0] <= sync <= SYNC_LIMITS[1]:
            raise ValueError(f"Синхронизация Google: от {SYNC_LIMITS[0]} до {SYNC_LIMITS[1]} минут")
    except ValueError as exc:
        message = str(exc) if "от " in str(exc) else "Введите целые числа"
        return redirect("/admin/settings", err=message)

    before = {**get_setting(db, BOOKING), "sync_minutes": get_setting(db, GOOGLE_SYNC).get("interval_minutes")}
    set_setting(db, BOOKING, value)
    set_setting(db, GOOGLE_SYNC, {"interval_minutes": sync})
    log_action(
        db, actor=user.login, action="settings.booking", entity_type="app_setting", entity_id=BOOKING,
        details=changed_fields(before, {**value, "sync_minutes": sync}), user_id=user.id,
    )
    from app.scheduler import reschedule_google_sync

    applied = reschedule_google_sync(sync)
    note = "" if applied else " — интервал синхронизации применится после перезапуска"
    return redirect("/admin/settings", ok="Настройки записи сохранены" + note)


@router.post("/reminders")
async def save_reminders(
    enabled: str = Form(""),
    hours: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    try:
        hours_before = reminder_service.parse_hours(hours)
    except ValueError as exc:
        return redirect("/admin/settings", err=str(exc))
    value = {"enabled": enabled == "on", "hours_before": hours_before}
    set_setting(db, REMINDERS, value)
    log_action(
        db,
        actor=user.login,
        action="settings.reminders",
        entity_type="app_setting",
        entity_id=REMINDERS,
        details=value,
        user_id=user.id,
    )
    return redirect("/admin/settings", ok="Настройки напоминаний сохранены")
