# 09-09 BOT_CALENDAR — платформа записи через Telegram + Google Calendar + AI

Система записи к специалисту: админ-панель, интеграция с Google Calendar
(OAuth, свой календарь у каждого сотрудника), Telegram-бот для клиентов
и AI-слой на OpenRouter для записи естественным языком.

## Что уже работает

- Админка с RBAC: сотрудники, услуги, клиенты, расписание, записи, аудит
- Расчёт свободных слотов: рабочее расписание − перерыв − брони − занятость Google
- Запись, перенос и отмена одной операцией в БД и в Google Calendar
- Защита от двойной брони: Redis-lock + проверка пересечений перед commit
- Обратная синхронизация: правки сотрудника в Google подтягиваются в БД
  (фоновая задача каждые 10 минут)

## В разработке

Telegram-бот (`app/bot/`), напоминания, AI-режим, мастер установки `/setup`.
Команда `python -m app.bot.main` появится вместе с ботом.

## Быстрый старт (разработка)

```bash
# 1. Инфраструктура (PostgreSQL + Redis)
docker compose -f docker-compose.dev.yml --env-file .env up -d

# 2. Зависимости
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Конфигурация
cp .env.example .env   # и заполнить своими ключами

# 4. Миграции БД
.venv\Scripts\alembic upgrade head

# 5. Первый администратор
.venv\Scripts\python -m app.cli create-admin

# 6. Запуск API + админки
.venv\Scripts\uvicorn app.main:app --reload
```

Админка: http://localhost:8000/admin  ·  Проверка: http://localhost:8000/health

## Проверка

```bash
.venv\Scripts\python -m pytest tests -q     # unit-тесты (нужен PostgreSQL)
.venv\Scripts\python scripts\smoke_admin.py # сквозной прогон админки
```

Тесты работают на отдельной базе `booking_test` в том же PostgreSQL и создают
её сами. Если PostgreSQL не поднят — тесты с БД пропускаются.

## Схема

```
Клиент → Telegram-бот ┐
                      ├→ booking_flow → PostgreSQL (+ Redis lock)
Админ  → Admin Panel ─┘        │
                               └→ google_event_id ↔ Google Calendar
                                          ↑
                        APScheduler: синхронизация Google → БД
```

Единая точка бронирования — `app/services/booking_flow.py`: и админка, и бот
ходят через неё, поэтому правила записи не расходятся между интерфейсами.
Источник истины по бизнесу — PostgreSQL, события живут в Google Calendar,
связывает их `booking.google_event_id`.

## Документация

| Файл | Назначение |
|---|---|
| docs/GOOGLE_SETUP.md | Создание OAuth Client ID (Google Cloud) |
| docs/TELEGRAM_SETUP.md | Создание бота в @BotFather |

Появятся к передаче проекта: INSTALL.md, OPENROUTER_SETUP.md, ADMIN.md,
TRANSFER.md, BACKUP.md.

## Принципы безопасности

- Секреты только в `.env` (в git не попадает), у заказчика — свои ключи.
- Refresh-токены Google шифруются (Fernet) перед записью в БД.
- Пароли — bcrypt. Пароль администратора создаётся при первом запуске.
- `google_event_id` хранится у каждой записи — правки идут в то же событие,
  дубли в календаре не появляются.
- Сбой Google не отменяет бронь: запись живёт в БД, ошибка уходит в аудит.
