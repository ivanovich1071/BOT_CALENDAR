"""Админка: компания, настройки ИИ, пакет компании, база знаний, диалоги."""

import json
from pathlib import Path

from sqlalchemy import select

import admin.routes.company as company_routes
from app.config.settings import Settings
from app.models.ai_message import AiMessage
from app.models.knowledge_article import KnowledgeArticle
from app.models.service import Service
from app.services.app_settings_service import AI, COMPANY, get_setting, set_setting

SEED = Path(__file__).resolve().parents[2] / "seed" / "vibemind.json"


def _ok(response) -> bool:
    return response.status_code == 303 and "ok=" in response.headers["location"]


def _err(response) -> bool:
    return response.status_code == 303 and "err=" in response.headers["location"]


def test_страница_компании_открывается(admin_client):
    page = admin_client.get("/admin/company")
    assert page.status_code == 200
    assert "Пакет компании" in page.text and "ИИ-консультант" in page.text


def test_профиль_компании_сохраняется(admin_client, db):
    response = admin_client.post(
        "/admin/company/profile",
        data={"name": "ВайбМайнд", "phone": "+375 29 7-200-700", "greeting": "Здравствуйте, {name}!"},
        follow_redirects=False,
    )
    assert _ok(response)
    company = get_setting(db, COMPANY)
    assert company["name"] == "ВайбМайнд" and company["greeting"] == "Здравствуйте, {name}!"


def test_профиль_без_названия_не_сохраняется(admin_client, db):
    assert _err(admin_client.post("/admin/company/profile", data={"phone": "1"}, follow_redirects=False))
    assert get_setting(db, COMPANY)["phone"] == ""


def test_настройки_ии_проверяются(admin_client, db):
    good = {
        "model": "qwen/qwen3-235b-a22b-2507", "temperature": "0,3",
        "hourly_limit": "40", "history_messages": "10", "history_hours": "3",
    }
    assert _ok(admin_client.post("/admin/company/ai", data=good, follow_redirects=False))
    assert get_setting(db, AI)["temperature"] == 0.3 and get_setting(db, AI)["hourly_limit"] == 40

    assert _err(admin_client.post("/admin/company/ai", data={**good, "hourly_limit": "много"}, follow_redirects=False))
    assert get_setting(db, AI)["hourly_limit"] == 40


def test_экспорт_отдаёт_файл(admin_client, service):
    response = admin_client.get("/admin/company/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert json.loads(response.content)["services"][0]["name"] == "Консультация"


def test_импорт_через_предпросмотр(admin_client, db):
    preview = admin_client.post(
        "/admin/company/import", files={"file": ("pack.json", SEED.read_bytes(), "application/json")}
    )
    assert preview.status_code == 200
    assert "Встреча-знакомство" in preview.text and "Применить" in preview.text
    assert db.scalars(select(Service)).all() == []  # предпросмотр ничего не меняет

    applied = admin_client.post(
        "/admin/company/import/apply", data={"pack_json": SEED.read_text(encoding="utf-8")}, follow_redirects=False
    )
    assert _ok(applied)
    assert len(db.scalars(select(Service)).all()) == 4
    assert get_setting(db, COMPANY)["name"] == "ВайбМайнд"


def test_не_json_возвращает_ошибку(admin_client):
    response = admin_client.post(
        "/admin/company/import", files={"file": ("pack.json", b"not json", "application/json")}, follow_redirects=False
    )
    assert _err(response)


def test_профиль_бота_в_telegram(admin_client, db, monkeypatch):
    applied: list[str] = []

    async def fake_apply(token, company):
        applied.append(company["name"])

    monkeypatch.setattr(company_routes, "apply_bot_profile", fake_apply)
    monkeypatch.setattr(company_routes, "get_settings", lambda: Settings(_env_file=None, bot_token="1:TEST"))
    set_setting(db, COMPANY, {"name": "ВайбМайнд"})

    assert _ok(admin_client.post("/admin/company/telegram", follow_redirects=False))
    assert applied == ["ВайбМайнд"]


def test_база_знаний_создание_правка_удаление(admin_client, db):
    assert _ok(
        admin_client.post(
            "/admin/knowledge/save",
            data={"title": "Цены", "body": "350 BYN за час", "sort_order": "1", "is_active": "on"},
            follow_redirects=False,
        )
    )
    [article] = db.scalars(select(KnowledgeArticle)).all()
    assert article.is_active and article.body == "350 BYN за час"

    admin_client.post(
        "/admin/knowledge/save",
        data={"article_id": str(article.id), "title": "Стоимость", "body": "350 BYN", "sort_order": "2"},
        follow_redirects=False,
    )
    db.refresh(article)
    assert (article.title, article.is_active) == ("Стоимость", False)
    assert "Стоимость" in admin_client.get("/admin/knowledge").text

    assert _ok(admin_client.post(f"/admin/knowledge/{article.id}/delete", follow_redirects=False))
    assert db.scalars(select(KnowledgeArticle)).all() == []


def test_диалог_клиента_виден(admin_client, db, client):
    db.add_all(
        [
            AiMessage(client_id=client.id, role="user", content="сколько стоит обучение?"),
            AiMessage(client_id=client.id, role="tool", content="{}", tool_name="find_slots"),
            AiMessage(client_id=client.id, role="assistant", content="2 800 BYN за группу"),
        ]
    )
    db.commit()

    listing = admin_client.get("/admin/dialogs")
    assert listing.status_code == 200 and "Пётр" in listing.text

    thread = admin_client.get(f"/admin/dialogs/{client.id}")
    assert "сколько стоит обучение?" in thread.text and "2 800 BYN за группу" in thread.text
    assert "find_slots" in thread.text
