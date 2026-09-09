# 09-09 BOT_CALENDAR — платформа записи через Telegram + Google Calendar + AI

Telegram-бот бронирования, админ-панель, интеграция с Google Calendar (OAuth,
календарь каждого сотрудника) и AI-слой на OpenRouter (Qwen) для записи
естественным языком.

## Быстрый старт (разработка)

```bash
# 1. Инфраструктура (PostgreSQL + Redis)
docker compose -f docker-compose.dev.yml --env-file .env up -d

# 2. Зависимости
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Конфигурация
cp .env.example .env   # и заполнить (ключи уже вписаны у разработчика)

# 4. Миграции БД
.venv\Scripts\alembic upgrade head

# 5. Первый администратор
.venv\Scripts\python -m app.cli create-admin

# 6. Запуск API + админки
.venv\Scripts\uvicorn app.main:app --reload

# 7. Запуск бота (отдельный терминал)
.venv\Scripts\python -m app.bot.main
```

Админка: http://localhost:8000/admin  Проверка: http://localhost:8000/health

## Схема

```
Клиент → Telegram-бот → Backend (FastAPI) → PostgreSQL (+ Redis lock)
                                │                └→ google_event_id ↔ Google Calendar
                                └→ AI (OpenRouter/Qwen) → intent-JSON → сервисы
Сотрудники → Admin Panel (RBAC) → OAuth Google → свой календарь
```

## Документация

| Файл | Назначение |
|---|---|
| docs/INSTALL.md | Установка с нуля |
| docs/GOOGLE_SETUP.md | Создание OAuth Client ID (Google Cloud) |
| docs/TELEGRAM_SETUP.md | Создание бота в @BotFather |
| docs/OPENROUTER_SETUP.md | Ключ AI |
| docs/ADMIN.md | Работа с админкой |
| docs/TRANSFER.md | Передача проекта заказчику |
| docs/BACKUP.md | Бэкапы и восстановление |

## Принципы безопасности

- Секреты только в `.env` (в git не попадает), у заказчика — свои ключи.
- Refresh-токены Google шифруются (Fernet) перед записью в БД.
- Пароли — bcrypt. Пароль администратора создаётся при первом запуске.
- `google_event_id` хранится у каждой записи — правки идут в то же событие.
- От двойной брони защищает Redis-lock + проверка БД и Google перед commit.
