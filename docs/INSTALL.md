# Установка

Два сценария: на машину разработчика (Windows) и на сервер (Ubuntu). Оба
приводят к одному результату — работают админка и бот, записи попадают в Google
Calendar.

> Продакшн-сборка в Docker (`Dockerfile`, `docker-compose.prod.yml`) — этап 6
> дорожной карты. Пока сервер поднимается на venv + systemd, как описано ниже;
> это рабочий вариант, а не временная заглушка.

---

## Что понадобится до начала

| Что | Где взять | Инструкция |
|---|---|---|
| Токен Telegram-бота | @BotFather | [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) |
| Google OAuth Client ID и Secret | console.cloud.google.com | [GOOGLE_SETUP.md](GOOGLE_SETUP.md) |
| Ключ OpenRouter | openrouter.ai/keys | [OPENROUTER_SETUP.md](OPENROUTER_SETUP.md) — нужен только для AI-режима, этап 5 |

Python 3.11 или новее. PostgreSQL 16 и Redis 7 — ставятся контейнерами, отдельно
устанавливать не нужно.

---

## Локально (Windows)

### 1. Инфраструктура

```bash
docker compose -f docker-compose.dev.yml --env-file .env up -d
```

Поднимает PostgreSQL на порту **5433** и Redis на 6379. Порт 5433, а не 5432,
намеренно: на машине разработчика часто уже стоит свой PostgreSQL, и конфликта
портов быть не должно.

### 2. Зависимости

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 3. Конфигурация

```bash
cp .env.example .env
```

Заполнить в `.env` как минимум:

| Переменная | Как получить |
|---|---|
| `SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ENCRYPTION_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `POSTGRES_PASSWORD` | Придумать. Он же должен стоять в `DATABASE_URL` |
| `DATABASE_URL` | `postgresql+psycopg://booking:ПАРОЛЬ@localhost:5433/booking` |
| `BOT_TOKEN` | От @BotFather |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Из Google Cloud |
| `GOOGLE_REDIRECT_URI` | Локально: `http://localhost:8000/oauth/google/callback` |

`ENCRYPTION_KEY` менять после запуска нельзя: им зашифрованы refresh-токены
Google в базе. Сменили — все сотрудники подключают календари заново.

### 4. База и первый администратор

```bash
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m app.cli create-admin
```

### 5. Запуск

Два окна терминала:

```bash
.venv\Scripts\uvicorn app.main:app --reload
```

```bash
.venv\Scripts\python -m app.bot.main
```

Админка — http://localhost:8000/admin.

---

## На сервер (Ubuntu 22.04+)

Дальше `example.com` — ваш домен, он должен уже указывать A-записью на IP
сервера. Все команды от пользователя с sudo.

### 1. Система

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git nginx
curl -fsSL https://get.docker.com | sudo sh
```

### 2. Код и окружение

```bash
sudo mkdir -p /opt/booking && sudo chown $USER /opt/booking
git clone https://github.com/ivanovich1071/BOT_CALENDAR.git /opt/booking
cd /opt/booking
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Инфраструктура и конфигурация

```bash
cp .env.example .env
nano .env
```

Отличия от локальной настройки:

```
APP_ENV=production
APP_BASE_URL=https://example.com
GOOGLE_REDIRECT_URI=https://example.com/oauth/google/callback
```

Этот же адрес добавьте в Google Cloud → Credentials → ваш OAuth-клиент →
Authorized redirect URIs. Расхождение хотя бы в одном символе даёт
`redirect_uri_mismatch`.

```bash
docker compose -f docker-compose.dev.yml --env-file .env up -d
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli create-admin
```

### 4. Автозапуск: два сервиса systemd

`/etc/systemd/system/booking-api.service`:

```ini
[Unit]
Description=Booking API + admin panel
After=network.target docker.service

[Service]
WorkingDirectory=/opt/booking
ExecStart=/opt/booking/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
User=booking

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/booking-bot.service`:

```ini
[Unit]
Description=Booking Telegram bot
After=network.target booking-api.service

[Service]
WorkingDirectory=/opt/booking
ExecStart=/opt/booking/.venv/bin/python -m app.bot.main
Restart=always
RestartSec=10
User=booking

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin booking
sudo chown -R booking /opt/booking
sudo systemctl daemon-reload
sudo systemctl enable --now booking-api booking-bot
```

`Restart=always` у бота не косметика: связь с `api.telegram.org` бывает
прерывистой, и процесс должен подниматься сам.

### 5. nginx и TLS

`/etc/nginx/sites-available/booking`:

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/booking /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
```

Certbot сам перепишет конфиг под HTTPS и настроит продление. HTTPS обязателен:
Google не примет `redirect_uri` на голом HTTP нигде, кроме `localhost`.

---

## Проверка после установки

```bash
curl -s https://example.com/health
```

Ожидаемо:

```json
{"status":"ok","env":"production","postgres":true,"redis":true,
 "scheduler":true,"google_configured":true}
```

`false` в любом поле — смотрите таблицу ошибок ниже.

Дальше по шагам:

1. Войти в `/admin` под созданным администратором.
2. `/admin/employees` — добавить специалиста.
3. `/admin/services` — добавить услугу с длительностью.
4. `/admin/schedule` — задать рабочие дни, часы и перерыв.
5. `/admin/calendars` — «Подключить Google», выбрать рабочий календарь.
6. `/admin/bookings/new` — создать запись и убедиться, что событие появилось
   в Google Calendar.
7. Открыть бота в Telegram, `/start`, пройти запись до конца.

Полный сквозной прогон админки одной командой:

```bash
.venv/bin/python scripts/smoke_admin.py
```

---

## Обновление

```bash
cd /opt/booking
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
sudo systemctl restart booking-api booking-bot
```

Перед `alembic upgrade` на боевой системе делайте дамп — [BACKUP.md](BACKUP.md).

---

## Если что-то не работает

| Симптом | Причина и что делать |
|---|---|
| `"postgres": false` | Контейнер не поднялся: `docker compose ps`, `docker logs bc_postgres`. Или пароль в `DATABASE_URL` не совпадает с `POSTGRES_PASSWORD` |
| `"redis": false` | `docker logs bc_redis`. Без Redis система работает, но состояние диалогов бота живёт в памяти процесса и теряется при перезапуске |
| `"google_configured": false` | Не заполнены `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. Настройки читаются при старте — после правки `.env` нужен перезапуск |
| `"scheduler": false` | Планировщик не стартовал: `journalctl -u booking-api -n 50`. Записи работают, обратная синхронизация с Google — нет |
| Бот не отвечает | `journalctl -u booking-bot -n 50`. Пустой `BOT_TOKEN` даёт явное сообщение при старте |
| `redirect_uri_mismatch` | Адрес в Google Cloud не совпадает с `GOOGLE_REDIRECT_URI` посимвольно |
| `access_denied` при подключении Google | Gmail сотрудника не добавлен в Test users — [GOOGLE_SETUP.md](GOOGLE_SETUP.md), шаг 4 |
| Alembic падает с `UnicodeDecodeError` | Только Windows с русской локалью. Обход: `alembic upgrade head --sql`, применить через `docker exec bc_postgres psql` |
