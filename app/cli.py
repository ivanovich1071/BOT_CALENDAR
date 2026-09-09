"""CLI-утилиты проекта. Запуск: python -m app.cli <команда>"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.config.security import hash_password
from app.db.database import SessionLocal
from app.models.user import User


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="Утилиты проекта")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-admin", help="Создать первого администратора")
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin()


if __name__ == "__main__":
    main()
