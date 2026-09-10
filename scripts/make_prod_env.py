"""Собирает .env для сервера из локального .env, не показывая значений.

    python scripts/make_prod_env.py calendar.1-2-3-4.nip.io

Ключи интеграций (бот, Google, OpenRouter) переносит как есть, а секреты самого
приложения генерирует заново: SECRET_KEY, ENCRYPTION_KEY, POSTGRES_PASSWORD,
BOT_WEBHOOK_SECRET. Результат — .env.production (в git не попадает).
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CARRY_OVER = (
    "BOT_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_TEMPERATURE",
    "OPENROUTER_MAX_TOKENS",
    "TIMEZONE",
)
REQUIRED = ("BOT_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Использование: python scripts/make_prod_env.py <домен>")
    domain = sys.argv[1].strip().removeprefix("https://").rstrip("/")
    local = read_env(ROOT / ".env")

    missing = [key for key in REQUIRED if not local.get(key)]
    if missing:
        sys.exit("В локальном .env не заполнены: " + ", ".join(missing))

    base_url = f"https://{domain}"
    lines = [
        "# Сгенерировано scripts/make_prod_env.py.",
        "# Секреты приложения новые, ключи интеграций перенесены из локального .env.",
        "# DATABASE_URL и REDIS_URL собирает docker-compose.prod.yml.",
        "APP_ENV=production",
        f"APP_BASE_URL={base_url}",
        f"GOOGLE_REDIRECT_URI={base_url}/oauth/google/callback",
        f"SECRET_KEY={secrets.token_urlsafe(48)}",
        f"ENCRYPTION_KEY={secrets.token_urlsafe(32)}",
        # hex: пароль попадает в URL подключения, спецсимволы там ни к чему
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
        f"BOT_WEBHOOK_SECRET={secrets.token_urlsafe(24)}",
    ]
    carried = [key for key in CARRY_OVER if local.get(key)]
    lines += [f"{key}={local[key]}" for key in carried]

    out = ROOT / ".env.production"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Записан {out.name} для {base_url}")
    print("Перенесены:", ", ".join(carried))
    print("Сгенерированы заново: SECRET_KEY, ENCRYPTION_KEY, POSTGRES_PASSWORD, BOT_WEBHOOK_SECRET")


if __name__ == "__main__":
    main()
