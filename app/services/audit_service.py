"""Аудит: фиксация действий в журнале."""

import logging

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger("audit")


def log_action(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    details: dict | None = None,
    user_id: int | None = None,
) -> None:
    """Записывает действие в audit_logs. Падение аудита не ломает бизнес-операцию."""
    try:
        db.add(
            AuditLog(
                user_id=user_id,
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                details=details,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — аудит не должен ломать основной сценарий
        db.rollback()
        logger.exception("Не удалось записать аудит: %s/%s", actor, action)
