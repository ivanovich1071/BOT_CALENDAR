# Резервные копии

Что копировать, как восстанавливать и как убедиться, что копия рабочая.

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
docker exec bc_postgres pg_dump -U booking -d booking -Fc > booking_$(date +%F).dump
```

Флаг `-Fc` — сжатый формат: меньше по размеру и восстанавливается выборочно.

Копия `.env` рядом:

```bash
cp /opt/booking/.env env_$(date +%F).backup
```

---

## Автоматически, раз в сутки

Скрипт `/opt/booking/backup.sh`:

```bash
#!/bin/bash
set -e
DIR=/var/backups/booking
DAY=$(date +%F)
mkdir -p "$DIR"

docker exec bc_postgres pg_dump -U booking -d booking -Fc > "$DIR/db_$DAY.dump"
cp /opt/booking/.env "$DIR/env_$DAY.backup"

# Держим 14 дней
find "$DIR" -name 'db_*.dump' -mtime +14 -delete
find "$DIR" -name 'env_*.backup' -mtime +14 -delete
```

```bash
sudo chmod +x /opt/booking/backup.sh
sudo chmod 700 /var/backups/booking
sudo crontab -e
```

Строка в crontab — каждый день в 3:30 ночи:

```
30 3 * * * /opt/booking/backup.sh >> /var/log/booking-backup.log 2>&1
```

`chmod 700` не формальность: в дампе лежат телефоны и имена клиентов, читать их
всем пользователям сервера незачем.

### Копия за пределами сервера

Всё, что описано выше, лежит на том же диске, что и сама система. Диск умер —
умерло всё. Добавьте выгрузку в другое место:

```bash
rsync -az /var/backups/booking/ backup@другой-сервер:/backups/booking/
```

Или любое S3-совместимое хранилище через `rclone`. Правило простое: копия,
которая живёт рядом с оригиналом, — не копия.

---

## Восстановление

### Полное, на чистом сервере

1. Пройти [INSTALL.md](INSTALL.md) до шага с миграциями **включительно**
   (`alembic upgrade head` можно пропустить — структура придёт из дампа).
2. Вернуть `.env` из копии.
3. Залить базу:

```bash
docker exec -i bc_postgres pg_restore -U booking -d booking --clean --if-exists < db_2026-09-09.dump
```

4. Догнать миграции, если код новее дампа:

```bash
.venv/bin/alembic upgrade head
```

5. Запустить и проверить:

```bash
sudo systemctl start booking-api booking-bot
curl -s http://127.0.0.1:8000/health
```

`--clean --if-exists` перезаписывает существующие таблицы. На боевой базе это
означает потерю всего, что появилось после дампа, — команда делает ровно то,
что написано.

### Проверить копию, ничего не сломав

Разворачивать дамп в отдельную базу — единственный способ узнать, что копия
рабочая, до того как она понадобится:

```bash
docker exec bc_postgres createdb -U booking booking_check
docker exec -i bc_postgres pg_restore -U booking -d booking_check < db_2026-09-09.dump
docker exec bc_postgres psql -U booking -d booking_check -c "select count(*) from bookings;"
docker exec bc_postgres dropdb -U booking booking_check
```

Разумно делать это раз в месяц. Копия, которую ни разу не пробовали
восстановить, — это надежда, а не резервная копия.

---

## Перед обновлением системы

```bash
docker exec bc_postgres pg_dump -U booking -d booking -Fc > before_update.dump
```

Миграции могут менять структуру необратимо. Тридцать секунд на дамп дешевле
любого разбирательства потом.

---

## Персональные данные

В дампе — имена, телефоны и `telegram_id` клиентов. Копии стоит хранить
зашифрованными, а срок хранения ограничить: две недели ежедневных плюс
несколько месячных — обычно достаточно и для аварий, и для здравого смысла.
