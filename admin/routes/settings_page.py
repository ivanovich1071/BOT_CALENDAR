from fastapi import APIRouter, Depends, Request

from admin.templating import render
from app.api.dependencies import require_permission
from app.config.settings import get_settings

router = APIRouter(prefix="/admin/settings")


@router.get("")
async def settings_page(
    request: Request,
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
    return render(
        request,
        "settings/index.html",
        {
            "user": user,
            "nav": "settings",
            "integrations": integrations,
            "app_env": s.app_env,
            "timezone": s.timezone,
            "page_title": "Настройки",
        },
    )
