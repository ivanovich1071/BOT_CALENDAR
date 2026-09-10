# Резервные копии

Что копировать, как восстанавливать и как убедиться, что копия рабочая.
Команды — для сервера, установленного по [INSTALL.md](INSTALL.md): код в
`/opt/bot-calendar`, всё в Docker.

---

## Что нужно копировать

| Что | Почему | Восстановимо иначе? |
|---|---|---|
| **База PostgreSQL** | Записи, клиенты, расписание, токены Google, журнал | Нет |
| **Файл `.env`** | Ключи и, главное, `ENCRYPTION_KEY` | Нет |
| Код | В GitHub | Да, `git clone` |
| Redis | Только состояние диалогов и блокировки | Не нужно |

### Почему `.env` не менее важен, чем база

`ENCRYPTION_KEY` — ключ, которым зашифрованы refresh-токены Google. Восстановить
базу без него можно, но все сотрудники будут подключать календари заново.
Потерять оба одновременно проще всего: они лежат на одном сервере. Копия
`.env` должна храниться **не там же**.

Хранить `.env` в менеджере паролей или в зашифрованном архиве — нормально.
В git — нет, он для того и в `.gitignore`.

---

## Ручная копия

```bash
cd /opt/bot-calendar
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U booking -d booking -Fc > booking_$(date +%F).dump
cp .env env_$(date +%F).backup
```

Флаг `-Fc` — сжатый формат: меньше по размеру и восстанавливается выборочно.
`-T` обязателен: без него docker испортит бинарный дамп символами терминала.

---

## Автоматически, раз в сутки

Скрипт `/opt/bot-calendar-backup.sh` — вне каталога с кодом, чтобы `git reset` при
деплое его не трогал:

```bash
#!/bin/bash
set -euo pipefail
DIR=/var/backups/bot-calendar
DAY=$(date +%F)
mkdir -p "$DIR"
cd /opt/bot-calendar

docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U booking -d booking -Fc > "$DIR/db_$DAY.dump"
cp .env "$DIR/env_$DAY.backup"

# Держим 14 дней
find "$DIR" -name 'db_*.dump' -mtime +14 -delete
find "$DIR" -name 'env_*.backup' -mtime +14 -delete
```

```bash
chmod 700 /opt/bot-calendar-backup.sh
mkdir -p /var/backups/bot-calendar && chmod 700 /var/backups/bot-calendar
crontab -e
```

Строка в crontab — каждый день в 3:30 ночи:

```
30 3 * * * /opt/bot-calendar-backup.sh >> /var/log/bot-calendar-backup.log 2>&1
```

`chmod 700` не формальность: в дампе лежат телефоны и имена клиентов, а в копии
`.env` — все ключи.

На сервере с 10 ГБ диска следите за местом: `df -h /`. Дамп небольшой базы
записей — единицы мегабайт, но 14 копий плюс образы Docker со временем
набегают.

### Копия за пределами сервера

Всё, что описано выше, лежит на том же диске, что и сама система. Диск умер —
умерло всё. Добавьте выгрузку в другое место:

```bash
rsync -az /var/backups/bot-calendar/ backup@другой-сервер:/backups/bot-calendar/
```

Или любое S3-совместимое хранилище через `rclone`. Правило простое: копия,
которая живёт рядом с оригиналом, — не копия.

---

## Восстановление

### Полное, на чистом сервере

1. Пройти [INSTALL.md](INSTALL.md) до запуска контейнеров, вернуть `.env` из
   копии — **с тем же** `ENCRYPTION_KEY`.
2. Поднять только базу:

```bash
cd /opt/bot-calendar
docker compose -f docker-compose.prod.yml up -d postgres
```

3. Залить дамп:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U booking -d booking --clean --if-exists < db_2026-09-10.dump
```

4. Поднять остальное — миграции догонят схему, если код новее дампа:

```bash
docker compose -f docker-compose.prod.yml up -d
curl -s http://127.0.0.1:8000/health
```

`--clean --if-exists` перезаписывает существующие таблицы. На боевой базе это
означает потерю всего, что появилось после дампа, — команда делает ровно то, что
написано.

### Проверить копию, ничего не сломав

Развернуть дамп в отдельную базу — единственный способ узнать, что копия
рабочая, до того как она понадобится:

```bash
cd /opt/bot-calendar
DC="docker compose -f docker-compose.prod.yml exec -T postgres"
$DC createdb -U booking booking_check
$DC pg_restore -U booking -d booking_check < /var/backups/bot-calendar/db_2026-09-10.dump
$DC psql -U booking -d booking_check -c "select count(*) from bookings;"
$DC dropdb -U booking booking_check
```

Разумно делать это раз в месяц. Копия, которую ни разу не пробовали
восстановить, — это надежда, а не резервная копия.

---

## Перед обновлением системы

```bash
cd /opt/bot-calendar
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U booking -d booking -Fc > /var/backups/bot-calendar/before_update.dump
```

Миграции применяются сами при старте API и могут менять структуру необратимо.
Тридцать секунд на дамп дешевле любого разбирательства потом.

---

## Персональные данные

В дампе — имена, телефоны и `telegram_user_id` клиентов. Копии стоит хранить
зашифрованными, а срок хранения ограничить: две недели ежедневных плюс несколько
месячных — обычно достаточно и для аварий, и для здравого смысла.
