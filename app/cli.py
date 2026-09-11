"""CLI-утилиты проекта. Запуск: python -m app.cli <команда>"""

import argparse
import getpass
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.config.security import hash_password
from app.db.database import SessionLocal
from app.models.booking import Booking
from app.models.enums import BOOKED, BOOKING_STATUS_LABELS_RU
from app.models.user import User
from app.services import booking_flow, company_pack, demo_service
from app.services.audit_service import log_action
from app.services.schedule_service import local_tz


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(f"{question} [y/N]: ").strip().lower() == "y"
    except EOFError:  # запуск без терминала — считаем отказом
        return False


def create_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.scalars(select(User).where(User.role == "admin")).first()
        if existing:
            answer = input(f"Админ '{existing.login}' уже существует. Создать ещё одного? [y/N]: ")
            if answer.strip().lower() != "y":
                print("Отменено.")
                return

        login = input("Логин администратора: ").strip()
        if not login:
            print("Логин не может быть пустым.")
            sys.exit(1)
        if db.scalar(select(User).where(User.login == login)):
            print(f"Логин '{login}' уже занят.")
            sys.exit(1)

        password = getpass.getpass("Пароль (мин. 8 символов): ")
        if len(password) < 8:
            print("Пароль слишком короткий.")
            sys.exit(1)
        password2 = getpass.getpass("Повторите пароль: ")
        if password != password2:
            print("Пароли не совпадают.")
            sys.exit(1)

        user = User(login=login, password_hash=hash_password(password), role="admin")
        db.add(user)
        db.commit()
        print(f"Готово. Администратор '{login}' создан (id={user.id}).")
    finally:
        db.close()


def import_company(path: str, yes: bool) -> None:
    """Загружает пакет компании: сначала показывает, что изменится."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        pack = company_pack.validate_pack(raw)
    except (OSError, json.JSONDecodeError, company_pack.PackError) as e:
        print(f"Пакет не загружен: {e}")
        sys.exit(1)
    db = SessionLocal()
    try:
        plan = company_pack.plan_import(db, pack)
        print("\n".join(plan.lines()))
        if not _confirm("Применить пакет?", yes):
            print("Отменено.")
            return
        company_pack.import_pack(db, pack, actor="cli")
        print(f"Готово. Загружена компания «{plan.company}».")
    finally:
        db.close()


def import_knowledge(path: str, yes: bool) -> None:
    """Обновляет только базу знаний из пакета: услуги и сотрудники из админки остаются как есть."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        pack = company_pack.validate_pack(raw)
    except (OSError, json.JSONDecodeError, company_pack.PackError) as e:
        print(f"Пакет не загружен: {e}")
        sys.exit(1)
    db = SessionLocal()
    try:
        print(f"База знаний «{pack['company']['name']}»: статей в пакете {len(pack['knowledge'])}, текущие будут заменены")
        if not _confirm("Заменить базу знаний?", yes):
            print("Отменено.")
            return
        count = company_pack.import_knowledge(db, pack, actor="cli")
        print(f"Готово: статей {count}.")
    finally:
        db.close()


def export_company(path: str | None) -> None:
    db = SessionLocal()
    try:
        text = json.dumps(company_pack.export_pack(db), ensure_ascii=False, indent=2)
    finally:
        db.close()
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
        print(f"Сохранено: {path}")
    else:
        print(text)


def demo_reset(yes: bool) -> None:
    """Очистка демо от тестовых записей: будущие отменяются (события уходят из Google),
    затем все записи удаляются. Клиенты, сотрудники и услуги остаются."""
    db = SessionLocal()
    try:
        bookings = db.scalars(select(Booking).order_by(Booking.start_at)).all()
        if not bookings:
            print("Записей нет — чистить нечего.")
            return
        now = datetime.now(timezone.utc)
        future = [b for b in bookings if b.status == BOOKED and b.start_at > now]
        print(f"Записей: {len(bookings)}, из них будущих активных: {len(future)}")
        for b in bookings:
            start = b.start_at.astimezone(local_tz()).strftime("%d.%m %H:%M")
            status = BOOKING_STATUS_LABELS_RU.get(b.status, b.status)
            client = b.client.name or b.client.telegram_username or f"клиент #{b.client_id}"
            print(f"  #{b.id} {start} · {b.employee.name} · {b.service.name} · {client} · {status}")
        if not _confirm("Отменить будущие и удалить ВСЕ эти записи?", yes):
            print("Отменено.")
            return
        for b in future:
            booking_flow.cancel(db, b.id, actor="cli:demo-reset")
        ids = [b.id for b in bookings]
        db.execute(delete(Booking).where(Booking.id.in_(ids)))
        db.commit()
        log_action(
            db, actor="cli", action="demo.reset", entity_type="booking", details={"deleted": len(ids)}
        )
        print(f"Готово: отменено {len(future)}, удалено {len(ids)}.")
    finally:
        db.close()


def demo_setup() -> None:
    """Демо-специалист и общие логины гостей; повторный запуск пароли не меняет."""
    db = SessionLocal()
    try:
        logins = demo_service.setup(db, actor="cli")
    except demo_service.DemoError as e:
        print(f"Демо-доступ не настроен: {e}")
        sys.exit(1)
    finally:
        db.close()
    print("Демо-доступ готов. Показ логинов на странице входа включается в «Настройках».")
    for item in logins:
        print(f"  {item['label']}: {item['login']} / {item['password']}")


def demo_reset_guest(yes: bool) -> None:
    """То же, что ночной сброс: удаляет пробы гостей, записи из бота не трогает."""
    if not _confirm("Удалить записи и клиентов, созданных гостями, и вернуть Демо-специалиста к исходному?", yes):
        print("Отменено.")
        return
    db = SessionLocal()
    try:
        result = demo_service.reset(db, actor="cli")
    finally:
        db.close()
    print(f"Готово: удалено записей {result['bookings']}, клиентов {result['clients']}.")


def ai_model(model: str | None) -> None:
    """Показать или сменить модель ИИ-консультанта (пусто — берётся OPENROUTER_MODEL из .env)."""
    from app.config.settings import get_settings
    from app.services.app_settings_service import AI, get_setting, set_setting

    db = SessionLocal()
    try:
        config = get_setting(db, AI)
        if model is None:
            print(f"Модель: {config.get('model') or get_settings().openrouter_model}")
            return
        set_setting(db, AI, {**config, "model": model.strip()})
        log_action(db, actor="cli", action="settings.ai", entity_type="settings", details={"model": model.strip()})
        print(f"Модель ИИ-консультанта: {model.strip() or get_settings().openrouter_model}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="Утилиты проекта")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-admin", help="Создать первого администратора")

    p_import = sub.add_parser("import-company", help="Загрузить пакет компании (JSON)")
    p_import.add_argument("path")
    p_import.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")

    p_kb = sub.add_parser("import-knowledge", help="Заменить только базу знаний из пакета компании")
    p_kb.add_argument("path")
    p_kb.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")

    p_export = sub.add_parser("export-company", help="Выгрузить пакет компании (JSON)")
    p_export.add_argument("path", nargs="?")

    p_reset = sub.add_parser("demo-reset", help="Отменить и удалить ВСЕ записи (чистка перед сдачей)")
    p_reset.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")

    sub.add_parser("demo-setup", help="Создать демо-доступ: Демо-специалист и логины гостей")

    p_guest = sub.add_parser("demo-reset-guest", help="Удалить пробы гостей демо-доступа (как ночной сброс)")
    p_guest.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")

    p_model = sub.add_parser("ai-model", help="Показать или сменить модель ИИ-консультанта")
    p_model.add_argument("model", nargs="?")

    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin()
    elif args.cmd == "import-company":
        import_company(args.path, args.yes)
    elif args.cmd == "import-knowledge":
        import_knowledge(args.path, args.yes)
    elif args.cmd == "export-company":
        export_company(args.path)
    elif args.cmd == "demo-reset":
        demo_reset(args.yes)
    elif args.cmd == "demo-setup":
        demo_setup()
    elif args.cmd == "demo-reset-guest":
        demo_reset_guest(args.yes)
    elif args.cmd == "ai-model":
        ai_model(args.model)


if __name__ == "__main__":
    main()
