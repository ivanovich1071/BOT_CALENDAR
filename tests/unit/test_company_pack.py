"""Пакет компании: шаблон ВайбМайнд, импорт поверх существующих данных, экспорт."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.enums import EXC_DAY_OFF
from app.models.google_account import GoogleAccount
from app.models.knowledge_article import KnowledgeArticle
from app.models.service import Service
from app.models.user import User
from app.services import company_pack
from app.services.app_settings_service import COMPANY, get_setting

SEED = Path(__file__).resolve().parents[2] / "seed" / "vibemind.json"


def _raw() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


def _pack() -> dict:
    return company_pack.validate_pack(_raw())


def _employee(db, name: str) -> Employee:
    return db.scalar(select(Employee).where(Employee.name == name))


def test_шаблон_вайбмайнд_проходит_проверку():
    pack = _pack()
    assert pack["company"]["name"] == "ВайбМайнд"
    assert len(pack["services"]) == 4
    assert [e["name"] for e in pack["employees"]] == ["Вероника Николаевна", "Наталья Александровна", "Евгений"]
    assert len(pack["knowledge"]) >= 10


def test_импорт_заводит_услуги_сотрудников_и_базу_знаний(db):
    company_pack.import_pack(db, _pack(), actor="test")

    assert get_setting(db, COMPANY)["name"] == "ВайбМайнд"
    assert db.scalar(select(Service).where(Service.name == "Встреча-знакомство")).price == 0
    evgeny = _employee(db, "Евгений")
    assert [s.name for s in evgeny.services] == ["Диагностика задачи или процесса"]
    assert len(evgeny.schedules) == 5
    veronika = _employee(db, "Вероника Николаевна")
    assert [x.kind for x in veronika.exceptions] == [EXC_DAY_OFF]
    assert len(db.scalars(select(KnowledgeArticle)).all()) == len(_pack()["knowledge"])


def test_база_знаний_обновляется_без_услуг_и_сотрудников(db):
    """Исполнители и цены, поправленные в админке, не откатываются к пакету."""
    company_pack.import_pack(db, _pack(), actor="test")
    consult = db.scalar(select(Service).where(Service.name == "Консультация по внедрению ИИ"))
    consult.price = 150
    veronika = _employee(db, "Вероника Николаевна")
    veronika.services = [*veronika.services, consult]
    db.add(KnowledgeArticle(title="Лишняя", body="уйдёт"))
    db.commit()

    assert company_pack.import_knowledge(db, _pack(), actor="test") == len(_pack()["knowledge"])

    db.expire_all()
    assert consult.price == 150 and consult in _employee(db, "Вероника Николаевна").services
    titles = [a.title for a in db.scalars(select(KnowledgeArticle))]
    assert "Лишняя" not in titles and len(titles) == len(_pack()["knowledge"])


def test_база_знаний_не_называет_исполнителей_услуг():
    """Кто проводит услугу — только из админки: иначе ИИ путает людей, когда их поменяли."""
    text = " ".join(a["body"] for a in _pack()["knowledge"])
    assert "Проводит" not in text and "Проводят" not in text


def test_существующий_сотрудник_сохраняет_логин_телефон_и_google(db):
    user = User(login="ivanovich", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(user)
    db.commit()
    evgeny = Employee(name="Евгений", phone="+375296470919", user_id=user.id, is_active=False)
    db.add(evgeny)
    db.commit()
    db.add(GoogleAccount(employee_id=evgeny.id, google_email="demo@example.com"))
    db.commit()

    company_pack.import_pack(db, _pack(), actor="test")

    same = _employee(db, "Евгений")
    assert same.id == evgeny.id
    assert same.phone == "+375296470919"
    assert same.user_id == user.id
    assert same.is_active is True
    assert len(same.google_accounts) == 1


def test_лишнее_уходит_в_архив(db, service, employee):
    plan = company_pack.plan_import(db, _pack())
    assert plan.services_archived == ["Консультация"]
    assert plan.employees_archived == ["Иванов"]

    company_pack.import_pack(db, _pack(), actor="test")

    db.refresh(service)
    db.refresh(employee)
    assert service.archived_at is not None
    assert employee.archived_at is not None


def test_экспорт_после_импорта_совпадает_с_шаблоном(db):
    original = _pack()
    company_pack.import_pack(db, original, actor="test")

    exported = company_pack.validate_pack(company_pack.export_pack(db))

    assert exported["company"] == original["company"]
    assert exported["knowledge"] == original["knowledge"]
    assert exported["services"] == original["services"]
    by_name = {e["name"]: e for e in exported["employees"]}
    for e in original["employees"]:
        assert {**by_name[e["name"]], "services": sorted(by_name[e["name"]]["services"])} == {
            **e,
            "services": sorted(e["services"]),
        }


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"format": "что-то"}, "не пакет компании"),
        ({"company": {"name": ""}}, "company.name"),
        ({"services": [{"name": "Икс", "duration_minutes": 0}]}, "duration_minutes"),
    ],
)
def test_битый_пакет_даёт_понятную_ошибку(patch, message):
    raw = {**_raw(), **patch}
    with pytest.raises(company_pack.PackError, match=message):
        company_pack.validate_pack(raw)
