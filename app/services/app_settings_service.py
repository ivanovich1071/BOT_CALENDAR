"""Настройки в БД поверх .env: то, что заказчик меняет без правки файлов."""

import logging

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

# Ключи настроек
GOOGLE_SYNC = "google_sync"          # {"interval_minutes": 10}
REMINDERS = "reminders"              # {"hours_before": [24, 1]}

DEFAULTS: dict[str, dict] = {
    GOOGLE_SYNC: {"interval_minutes": 10},
    REMINDERS: {"hours_before": [24, 1]},
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
