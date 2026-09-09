from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.templating import render
from app.api.dependencies import require_permission
from app.db.database import get_db
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/admin/audit")


@router.get("")
async def audit_page(
    request: Request,
    actor: str = "",
    action: str = "",
    user=Depends(require_permission("view_audit")),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    if actor.strip():
        q = q.where(AuditLog.actor.ilike(f"%{actor.strip()}%"))
    if action.strip():
        q = q.where(AuditLog.action.ilike(f"%{action.strip()}%"))
    items = db.scalars(q).all()
    return render(
        request,
        "audit/list.html",
        {"user": user, "nav": "audit", "items": items, "f_actor": actor, "f_action": action, "page_title": "Журнал аудита"},
    )
