"""База знаний компании: статьи, которые целиком уходят ИИ-консультанту."""

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.diff import changed_fields
from admin.flash import redirect
from admin.templating import render
from app.ai.prompts.consultant import MAX_KNOWLEDGE_CHARS
from app.api.dependencies import require_permission
from app.db.database import get_db
from app.models.knowledge_article import KnowledgeArticle
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/admin/knowledge")


def _snapshot(article: KnowledgeArticle) -> dict:
    return {
        "title": article.title,
        "body": article.body,
        "sort_order": article.sort_order,
        "is_active": article.is_active,
    }


@router.get("")
async def list_articles(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    items = db.scalars(
        select(KnowledgeArticle).order_by(KnowledgeArticle.sort_order, KnowledgeArticle.id)
    ).all()
    total = sum(len(a.title) + len(a.body) for a in items if a.is_active)
    return render(
        request,
        "knowledge/list.html",
        {
            "user": user,
            "nav": "knowledge",
            "items": items,
            "total": total,
            "limit": MAX_KNOWLEDGE_CHARS,
            "page_title": "База знаний",
        },
    )


@router.get("/new")
async def new_article(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    next_order = len(db.scalars(select(KnowledgeArticle.id)).all()) + 1
    return render(
        request,
        "knowledge/form.html",
        {"user": user, "nav": "knowledge", "article": None, "next_order": next_order, "page_title": "Новая статья"},
    )


@router.get("/{article_id}/edit")
async def edit_article(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("manage_settings")),
):
    article = db.get(KnowledgeArticle, article_id)
    if article is None:
        return redirect("/admin/knowledge", err="Статья не найдена")
    return render(
        request,
        "knowledge/form.html",
        {"user": user, "nav": "knowledge", "article": article, "page_title": f"Статья: {article.title}"},
    )


@router.post("/save")
async def save_article(
    article_id: int = Form(0),
    title: str = Form(""),
    body: str = Form(""),
    sort_order: str = Form("0"),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    back = f"/admin/knowledge/{article_id}/edit" if article_id else "/admin/knowledge/new"
    title = title.strip()
    if not title or len(title) > 200:
        return redirect(back, err="Заголовок обязателен, до 200 символов")
    try:
        order = int(sort_order.strip() or 0)
    except ValueError:
        return redirect(back, err="Порядок — целое число")

    if article_id:
        article = db.get(KnowledgeArticle, article_id)
        if article is None:
            return redirect("/admin/knowledge", err="Статья не найдена")
        before = _snapshot(article)
    else:
        article = KnowledgeArticle()
        db.add(article)
        before = {}
    article.title = title
    article.body = body.strip()
    article.sort_order = order
    article.is_active = is_active == "on"
    db.commit()
    log_action(
        db,
        actor=user.login,
        action="knowledge.update" if before else "knowledge.create",
        entity_type="knowledge_article",
        entity_id=article.id,
        details=changed_fields(before, _snapshot(article)),
        user_id=user.id,
    )
    return redirect("/admin/knowledge", ok="Статья сохранена")


@router.post("/{article_id}/delete")
async def delete_article(
    article_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("manage_settings")),
):
    article = db.get(KnowledgeArticle, article_id)
    if article is None:
        return redirect("/admin/knowledge", err="Статья не найдена")
    title = article.title
    db.delete(article)
    db.commit()
    log_action(
        db, actor=user.login, action="knowledge.delete", entity_type="knowledge_article",
        entity_id=article_id, details={"title": title}, user_id=user.id,
    )
    return redirect("/admin/knowledge", ok=f"Статья «{title}» удалена")
