"""ИИ-консультант: диалог с клиентом поверх базы знаний и инструментов записи.

Цикл: история разговора → модель → инструменты (найти время, предложить запись,
показать записи) → ответ. Модель ничего не записывает: propose_booking лишь
готовит карточку, запись создаёт нажатие кнопки клиентом (app/bot/handlers/ai.py).

В модель уходят текст клиента, база знаний и справочники компании — без имени,
телефона и Telegram ID клиента.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai import tools
from app.ai.prompts.consultant import MAX_MESSAGE_CHARS, build_system_prompt
from app.bot import services
from app.bot.db import run_db
from app.integrations.openrouter.client import OpenRouterError, chat
from app.models.ai_message import AiMessage
from app.models.knowledge_article import KnowledgeArticle
from app.services.app_settings_service import AI, get_setting
from app.services.schedule_service import local_now

logger = logging.getLogger(__name__)

# Кругов «модель → инструменты» на одно сообщение клиента; последний — без инструментов
MAX_TOOL_ROUNDS = 4
# Результат инструмента в журнале диалога обрезается — для разбора хватает
TOOL_LOG_CHARS = 2000
HISTORY_RETENTION_DAYS = 30


@dataclass
class AgentReply:
    text: str = ""
    proposal: tools.Proposal | None = None
    show_bookings: bool = False
    failed: bool = False


def ai_config(db: Session) -> dict:
    return get_setting(db, AI)


def load_context(db: Session, client_id: int) -> dict:
    """Всё, из чего собирается запрос к модели: компания, база знаний, справочники, история."""
    config = ai_config(db)
    since = datetime.now(timezone.utc) - timedelta(hours=float(config.get("history_hours") or 3))
    rows = db.scalars(
        select(AiMessage)
        .where(
            AiMessage.client_id == client_id,
            AiMessage.role.in_(("user", "assistant")),
            AiMessage.created_at >= since,
        )
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(int(config.get("history_messages") or 12))
    ).all()
    articles = db.scalars(
        select(KnowledgeArticle)
        .where(KnowledgeArticle.is_active.is_(True))
        .order_by(KnowledgeArticle.sort_order, KnowledgeArticle.id)
    ).all()
    return {
        "config": config,
        "company": services.company_profile(db),
        "articles": [{"title": a.title, "body": a.body} for a in articles],
        "services": services.active_services(db),
        "employees": services.employees_with_schedule(db),
        "history": [{"role": r.role, "content": r.content} for r in reversed(rows)],
    }


def save_message(db: Session, client_id: int, role: str, content: str, tool_name: str | None = None) -> None:
    db.add(AiMessage(client_id=client_id, role=role, content=content, tool_name=tool_name))
    db.commit()


def purge_old_messages(db: Session, days: int = HISTORY_RETENTION_DAYS) -> int:
    """Диалоги хранятся ограниченное время — вызывается планировщиком."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = db.execute(delete(AiMessage).where(AiMessage.created_at < cutoff))
    db.commit()
    return result.rowcount or 0


async def respond(client_id: int, text: str) -> AgentReply:
    """Ответ консультанта на сообщение клиента. Сбой модели — failed=True, без исключений."""
    context = await run_db(load_context, client_id)
    config = context["config"]
    system = build_system_prompt(
        company=context["company"],
        articles=context["articles"],
        services=context["services"],
        employees=context["employees"],
        today=local_now().date(),
    )
    user_text = text.strip()[:MAX_MESSAGE_CHARS]
    messages = [{"role": "system", "content": system}, *context["history"], {"role": "user", "content": user_text}]
    await run_db(save_message, client_id, "user", user_text)

    ctx = tools.ToolContext(client_id=client_id)
    answer = ""
    try:
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            last_round = round_no == MAX_TOOL_ROUNDS
            reply = await chat(
                messages,
                tools=None if last_round else tools.TOOLS,
                model=config.get("model") or None,
                temperature=config.get("temperature"),
            )
            if not reply.tool_calls or last_round:
                answer = reply.content.strip()
                break
            messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": reply.tool_calls})
            for call in reply.tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await run_db(tools.run_tool, ctx, name, args)
                payload = json.dumps(result, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": payload})
                await run_db(save_message, client_id, "tool", payload[:TOOL_LOG_CHARS], name)
    except OpenRouterError as exc:
        # Текст клиента в лог не пишем — только причину
        logger.warning("ИИ-консультант: ответа нет (%s)", exc)
        return AgentReply(failed=True)

    if not answer:
        if ctx.proposal:
            answer = "Подобрал вариант — проверьте и нажмите «Записаться»."
        elif ctx.show_bookings:
            answer = "Ваши записи:"
        else:
            return AgentReply(failed=True)
    await run_db(save_message, client_id, "assistant", answer)
    return AgentReply(text=answer, proposal=ctx.proposal, show_bookings=ctx.show_bookings)
