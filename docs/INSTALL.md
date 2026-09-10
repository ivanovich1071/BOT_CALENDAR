# Установка

Два сценария: на машину разработчика (Windows) — для доработки, и на сервер
(Ubuntu 24.04, Docker) — для работы. Оба приводят к одному результату: работают
админка и бот, записи попадают в Google Calendar.

---

## Что понадобится до начала

| Что | Где взять | Инструкция |
|---|---|---|
| Токен Telegram-бота | @BotFather | [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) |
| Google OAuth Client ID и Secret | console.cloud.google.com | [GOOGLE_SETUP.md](GOOGLE_SETUP.md) |
| Ключ OpenRouter | openrouter.ai/keys | [OPENROUTER_SETUP.md](OPENROUTER_SETUP.md) — для записи фразой; без ключа бот работает кнопками |

Для сервера дополнительно: VPS на Ubuntu 24.04 от 1 ГБ RAM и 10 ГБ диска,
лучше в Европе — чтобы Telegram, Google и OpenRouter были доступны без
сюрпризов.

---

## Локально (Windows)

### 1. Инфраструктура

```bash
docker compose -f docker-compose.dev.yml --env-file .env up -d
```

Поднимает PostgreSQL на порту **5433** и Redis на 6379. Порт 5433, а не 5432,
намеренно: на машине разработчика часто уже стоит свой PostgreSQL.

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

## На сервер (Ubuntu 24.04, Docker)

Всё работает в контейнерах: PostgreSQL, Redis, API с админкой и бот. Наружу
смотрит только nginx на хосте с сертификатом Let's Encrypt; порты базы и Redis
не публикуются, API слушает только `127.0.0.1`.

Дальше `IP` — адрес сервера, `DOMAIN` — адрес админки. Команды выполняются из
Git Bash в корне репозитория.

### Какой адрес выбрать

Google пускает OAuth только на HTTPS-домен, голый IP не подойдёт.

| Вариант | Как | Оговорка |
|---|---|---|
| Свой домен или поддомен | A-запись на IP сервера | Лучший вариант для боевой системы |
| Поддомен nip.io | `calendar.1-2-3-4.nip.io` — IP через дефисы, резолвится сам | nip.io нет в Public Suffix List: лимит Let's Encrypt общий для всех его пользователей, и сертификат иногда не выдаётся |
| DuckDNS | Бесплатный поддомен на duckdns.org с IP сервера | Нужна регистрация на duckdns.org. Домен есть в PSL — у него свой лимит |

### 1. Доступ по ключу

Деплой ходит на сервер по отдельному ключу без пароля. Положить ключ — один раз:
двойной клик по **`Подключить сервер.bat`** в корне проекта (адрес возьмёт из
`.env.deploy`, иначе спросит) или в Git Bash:

```bash
bash scripts/add_deploy_key.sh IP
```

Скрипт создаст ключ `~/.ssh/bot_calendar_deploy`, если его ещё нет, положит его
на сервер — здесь один раз спросит пароль root, и вводит его человек, — и сам
проверит вход без пароля. Повторный запуск не дублирует ключ.

### 2. Подготовка сервера

Один раз:

```bash
ssh -i ~/.ssh/bot_calendar_deploy root@IP 'bash -s' -- DOMAIN < scripts/server_bootstrap.sh
```

Скрипт ставит Docker, nginx и certbot из репозитория Ubuntu, создаёт swap на
2 ГБ (при 1 ГБ RAM без него сборка рискует упереться в память), открывает в
файрволе только 22, 80 и 443, клонирует код в `/opt/bot-calendar` и настраивает
nginx на `DOMAIN`. Повторный запуск безопасен.

### 3. Конфигурация

**Если на вашей машине есть заполненный `.env`** — соберите из него серверный.
Ключи интеграций переносятся, секреты приложения генерируются заново, значения
нигде не печатаются:

```bash
python scripts/make_prod_env.py DOMAIN
scp -i ~/.ssh/bot_calendar_deploy .env.production root@IP:/opt/bot-calendar/.env
ssh -i ~/.ssh/bot_calendar_deploy root@IP "chmod 600 /opt/bot-calendar/.env"
```

**Иначе** — прямо на сервере: `cp .env.example .env` и заполнить `APP_ENV=production`,
`APP_BASE_URL=https://DOMAIN`, `GOOGLE_REDIRECT_URI=https://DOMAIN/oauth/google/callback`,
`SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD` (только латиница и цифры — он
попадает в строку подключения), `BOT_TOKEN`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `OPENROUTER_API_KEY`. `DATABASE_URL` и `REDIS_URL`
заполнять не нужно — их собирает `docker-compose.prod.yml`.

### 4. Сертификат

```bash
ssh -i ~/.ssh/bot_calendar_deploy root@IP "certbot --nginx -d DOMAIN --non-interactive --agree-tos --register-unsafely-without-email --redirect"
```

`--agree-tos` — это согласие владельца сервера с условиями Let's Encrypt. Без
email писем об истечении не будет, но они и не нужны: продление идёт само по
таймеру certbot.

### 5. Запуск

Если бот с этим токеном запущен где-то ещё — например, локально, — остановите
его: второй экземпляр Telegram отклонит.

```bash
ssh -i ~/.ssh/bot_calendar_deploy root@IP "cd /opt/bot-calendar && docker compose -f docker-compose.prod.yml up -d --build"
```

Первая сборка — 3–5 минут. Миграции применяются сами перед стартом API, бот
стартует после API.

### 6. Администратор

```bash
ssh -t -i ~/.ssh/bot_calendar_deploy root@IP "cd /opt/bot-calendar && docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin"
```

Пароль вводит владелец, в скриптах и логах его нет.

### 7. Google

В Google Cloud → Credentials → ваш OAuth-клиент → Authorized redirect URIs
добавьте `https://DOMAIN/oauth/google/callback`. Gmail каждого сотрудника,
который будет подключать календарь, — в Audience → Test users.

---

## Проверка после установки

```bash
curl -s https://DOMAIN/health
```

Ожидаемо:

```json
{"status":"ok","env":"production","postgres":true,"redis":true,
 "scheduler":true,"google_configured":true,"ai_configured":true}
```

`false` в любом поле — смотрите таблицу ниже.

Дальше по шагам:

1. Войти в `https://DOMAIN/admin`.
2. `/admin/employees`, `/admin/services`, `/admin/schedule` — специалист, услуга,
   рабочие дни.
3. `/admin/calendars` — «Подключить Google», выбрать рабочий календарь.
4. В боте: `/start` → «Записаться» до конца → событие появилось в Google Calendar.
5. В боте текстом: «хочу на консультацию завтра после обеда» → бот показал слоты.
6. `/admin/settings` — напоминания включены, `24, 1`.

---

## Обновление

С машины разработчика, одной командой:

```bash
bash scripts/deploy.sh
```

Адрес сервера — в файле `.env.deploy` в корне репозитория (в git не попадает):

```
DEPLOY_HOST=IP
DEPLOY_DOMAIN=DOMAIN
```

Скрипт отказывается работать, если есть незакоммиченные или неотправленные
изменения. Дальше в одной SSH-сессии — хостинги режут частые подключения —
подтягивает код, пересобирает контейнеры и проверяет `/health` изнутри и
снаружи по HTTPS.

Перед обновлением, которое меняет структуру базы, сделайте дамп — [BACKUP.md](BACKUP.md).

---

## Обслуживание

Команды выполняются на сервере в `/opt/bot-calendar`:

| Задача | Команда |
|---|---|
| Состояние контейнеров | `docker compose -f docker-compose.prod.yml ps` |
| Логи API / бота | `docker compose -f docker-compose.prod.yml logs -f --tail 100 api` (или `bot`) |
| Применить правку `.env` | `docker compose -f docker-compose.prod.yml up -d` |
| Расход памяти | `docker stats --no-stream` |

Логи ротируются сами: не больше 30 МБ на контейнер.

---

## Если что-то не работает

| Симптом | Причина и что делать |
|---|---|
| `"postgres": false` | `logs postgres`. Если `POSTGRES_PASSWORD` меняли после первого запуска — база в томе помнит старый пароль |
| `"redis": false` | `logs redis`. Без Redis система работает, но состояние диалогов живёт в памяти бота |
| `"google_configured": false` | Не заполнены `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, или `.env` правили без `up -d` |
| `"ai_configured": false` | Не заполнен `OPENROUTER_API_KEY` — бот работает кнопками |
| `"scheduler": false` | `logs api`. Записи работают, синхронизация с Google и напоминания — нет |
| Бот не отвечает | `logs bot`. `TelegramConflictError` — где-то запущен второй экземпляр с тем же токеном |
| certbot: `too many certificates` для nip.io | Лимит Let's Encrypt, общий для всех на nip.io. Перейти на DuckDNS или свой домен |
| `redirect_uri_mismatch` | Адрес в Google Cloud не совпадает с `GOOGLE_REDIRECT_URI` посимвольно |
| `access_denied` при подключении Google | Gmail сотрудника не добавлен в Test users — [GOOGLE_SETUP.md](GOOGLE_SETUP.md), шаг 4 |
| Сборка или контейнеры падают по памяти | `free -m` — должен быть swap 2 ГБ, его создаёт `server_bootstrap.sh` |
| SSH вдруг перестал пускать | Хостинг временно блокирует после частых подключений. Подождите 5–10 минут |
| Alembic падает с `UnicodeDecodeError` | Только Windows с русской локалью. Обход: `alembic upgrade head --sql`, применить через `docker exec bc_postgres psql` |
