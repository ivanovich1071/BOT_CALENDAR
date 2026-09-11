"""Настройки в БД поверх .env: то, что заказчик меняет без правки файлов."""

import logging

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

# Ключи настроек
GOOGLE_SYNC = "google_sync"          # {"enabled": true, "interval_minutes": 10}
GOOGLE_PENDING = "google_pending"    # события, удалённые у нас при выключенном Google
REMINDERS = "reminders"              # {"enabled": true, "hours_before": [24, 1]}
COMPANY = "company"                  # профиль компании: название, контакты, приветствие
AI = "ai"                            # модель и лимиты ИИ-консультанта (пусто — из .env)
BOOKING = "booking"                  # шаг сетки, горизонт записи
DEMO = "demo"                        # демо-доступ: логины гостей, показ на странице входа

COMPANY_FIELDS = (
    "name", "tagline", "description", "phone", "telegram", "website", "email",
    "currency", "greeting", "assistant_name", "ai_rules",
)

DEFAULTS: dict[str, dict] = {
    GOOGLE_SYNC: {"enabled": True, "interval_minutes": 10},
    GOOGLE_PENDING: {"deletes": []},
    REMINDERS: {"enabled": True, "hours_before": [24, 1]},
    COMPANY: {field: "" for field in COMPANY_FIELDS},
    AI: {"model": "", "temperature": None, "hourly_limit": 30, "history_messages": 12, "history_hours": 3},
    BOOKING: {"slot_step_minutes": 30, "horizon_days": 90, "min_lead_minutes": 0},
    DEMO: {"enabled": False, "show_on_login": False, "logins": []},
}


def get_setting(db: Session, key: str) -> dict:
    """Значение настройки, дополненное значениями по умолчанию для недостающих полей."""
    merged = dict(DEFAULTS.get(key, {}))
    try:
        row = db.get(AppSetting, key)
    except Exception:  # noqa: BLE001 — БД недоступна: работаем на умолчаниях
        logger.exception("Не удалось прочитать настройку %s", key)
        return merged
    if row and isinstance(row.value, dict):
        merged.update(row.value)
    return merged


def set_setting(db: Session, key: str, value: dict) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()
