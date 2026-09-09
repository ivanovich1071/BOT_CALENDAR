"""Smoke-проверка админки: вход, CRUD, расписание, слоты, Google, перенос, отмена.

Запуск: python scripts/smoke_admin.py  (требует поднятые postgres/redis)

Скрипт рассчитан на повторные запуски: рабочий день выбирается тот, где у
сотрудника ещё нет броней, поэтому прошлые прогоны не ломают проверки.
"""

import re
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.services.schedule_service import local_now, local_tz

c = TestClient(app, follow_redirects=False)
failures = []


def check(name: str, cond: bool, extra: str = ""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        failures.append(name)


# 0. Гарантируем наличие dev-админа (скрипт можно запускать повторно)
from app.config.security import hash_password
from app.db.database import SessionLocal
from app.models.booking import Booking
from app.models.user import User

_db = SessionLocal()
if not _db.query(User).filter(User.login == "admin").first():
    _db.add(User(login="admin", password_hash=hash_password("admin12345"), role="admin"))
    _db.commit()
_db.close()


# 1. Страница входа
r = c.get("/login")
check("GET /login", r.status_code == 200)

# 2. Неверный пароль → 401
r = c.post("/login", data={"login": "admin", "password": "wrong-pass"})
check("login с неверным паролем → 401", r.status_code == 401)

# 3. Вход админом
r = c.post("/login", data={"login": "admin", "password": "admin12345"})
check("login админом → 303", r.status_code == 303, str(r.status_code))

# 4. Дашборд
r = c.get("/admin/")
check("GET /admin/ дашборд", r.status_code == 200 and "Панель управления" in r.text)

# 5. Сотрудник: создать с логином
r = c.post(
    "/admin/employees/create",
    data={
        "name": "Иванов Тест",
        "phone": "+7 900 000-00-01",
        "specialization": "Мастер",
        "with_login": "on",
        "login": "ivanov",
        "password": "ivanov-pass-1",
        "permissions": ["view_clients"],
    },
)
check("создание сотрудника", r.status_code == 303)
r = c.get("/admin/employees")
check("сотрудник в списке", "Иванов Тест" in r.text)

# 6. Услуга
r = c.post(
    "/admin/services/create",
    data={"name": "Консультация", "duration_minutes": "60", "price": "2000", "description": ""},
)
check("создание услуги", r.status_code == 303)
r = c.get("/admin/services")
check("услуга в списке", "Консультация" in r.text)

# 7. Расписание (пн–пт 09:00–18:00, перерыв 13:00–14:00) — одним запросом
data = {"employee_id": "1"}
for d in range(7):
    data[f"start_{d}"] = ""
    data[f"end_{d}"] = ""
    data[f"break_start_{d}"] = ""
    data[f"break_end_{d}"] = ""
    if d < 5:
        data[f"active_{d}"] = "on"
        data[f"start_{d}"] = "09:00"
        data[f"end_{d}"] = "18:00"
        data[f"break_start_{d}"] = "13:00"
        data[f"break_end_{d}"] = "14:00"
r = c.post("/admin/schedule/save", data=data)
assert r.status_code == 303
r = c.get("/admin/schedule")
check("расписание сохранено и отображается", r.status_code == 200 and "09:00" in r.text)


def free_workday():
    """Ближайший будний день без броней сотрудника #1 — скрипт повторяем."""
    db = SessionLocal()
    try:
        day = local_now().date() + timedelta(days=1)
        for _ in range(60):
            if day.weekday() < 5:
                start = datetime.combine(day, time.min, tzinfo=local_tz()).astimezone(timezone.utc)
                taken = (
                    db.query(Booking)
                    .filter(
                        Booking.employee_id == 1,
                        Booking.status == "booked",
                        Booking.start_at < start + timedelta(days=1),
                        Booking.end_at > start,
                    )
                    .count()
                )
                if taken == 0:
                    return day
            day += timedelta(days=1)
        raise RuntimeError("не нашёл свободный будний день")
    finally:
        db.close()


day = free_workday()
slots_url = f"/admin/bookings/slots?employee_id=1&service_id=1&date={day.isoformat()}"

# 8. Слоты на свободный день: рабочий день начинается в 09:00
r = c.get(slots_url)
check("слоты рассчитаны", r.status_code == 200 and "09:00" in r.text, f"({day})")

# 9. Перерыв 13:00–14:00 не предлагается под услугу 60 минут
check("перерыв исключён из слотов", '<option value="13:00">' not in r.text)

# 10. Занятость Google убирает слот из выдачи
import app.services.booking_flow as booking_flow

busy_start = datetime.combine(day, time(11, 0), tzinfo=local_tz()).astimezone(timezone.utc)
_original_busy = booking_flow.calendar_service.get_busy_intervals
booking_flow.calendar_service.get_busy_intervals = lambda *a, **kw: [
    (busy_start, busy_start + timedelta(hours=1))
]
try:
    r_busy = c.get(slots_url)
finally:
    booking_flow.calendar_service.get_busy_intervals = _original_busy
check(
    "занятость Google убирает слот",
    '<option value="11:00">' not in r_busy.text and '<option value="12:00">' in r_busy.text,
)

# 11. Создание записи на первый свободный слот
m = re.search(r'<option value="(\d\d:\d\d)">', r.text)
slot = m.group(1) if m else "09:00"
r = c.post(
    "/admin/bookings/create",
    data={
        "employee_id": "1", "service_id": "1", "date": day.isoformat(),
        "slot": slot, "client_id": "0", "client_name": "Пётр Тестовый",
        "client_phone": "+7 900 000-00-02", "notes": "smoke test",
    },
)
check("создание записи из админки", r.status_code == 303 and "err" not in r.headers.get("location", ""))

# 12. Двойная бронь на тот же слот → отказ
r = c.post(
    "/admin/bookings/create",
    data={
        "employee_id": "1", "service_id": "1", "date": day.isoformat(),
        "slot": slot, "client_id": "0", "client_name": "Дублёр", "client_phone": "",
    },
)
loc = r.headers.get("location", "")
check("двойная бронь отклонена", "err" in loc, loc[-40:])

# 13. Список записей содержит бронь
r = c.get(f"/admin/bookings?date={day.isoformat()}")
check("запись видна в списке", "Пётр Тестовый" in r.text)

# 14. Перенос записи на другой свободный слот
_db = SessionLocal()
booking_id = (
    _db.query(Booking).filter(Booking.employee_id == 1).order_by(Booking.id.desc()).first().id
)
_db.close()
r = c.get(f"/admin/bookings/slots?employee_id=1&service_id=1&date={day.isoformat()}"
          f"&exclude_booking_id={booking_id}")
options = re.findall(r'<option value="(\d\d:\d\d)">', r.text)
new_slot = next((s for s in options if s != slot), None)
r = c.post(
    f"/admin/bookings/{booking_id}/reschedule",
    data={"date": day.isoformat(), "slot": new_slot or "16:00"},
)
check("перенос записи", r.status_code == 303 and "err" not in r.headers.get("location", ""),
      f"({slot} → {new_slot})")

# 15. Отмена записи (путь с удалением события в Google)
r = c.post(f"/admin/bookings/{booking_id}/status", data={"status": "cancelled"})
check("отмена записи", r.status_code == 303 and "err" not in r.headers.get("location", ""))
_db = SessionLocal()
cancelled = _db.get(Booking, booking_id).status == "cancelled"
_db.close()
check("статус в БД = cancelled", cancelled)

# 16. Настройки, календари и аудит
r = c.get("/admin/settings")
check("GET /admin/settings", r.status_code == 200)
r = c.get("/admin/calendars")
check("GET /admin/calendars", r.status_code == 200)
r = c.get("/admin/audit")
check("GET /admin/audit", r.status_code == 200 and "booking.create" in r.text)

# 17. RBAC: сотрудник без права view_audit получает 403
c2 = TestClient(app, follow_redirects=False)
c2.post("/login", data={"login": "ivanov", "password": "ivanov-pass-1"})
r = c2.get("/admin/audit")
check("RBAC: аудит запрещён сотруднику", r.status_code == 403, str(r.status_code))
r = c2.get("/admin/clients")
check("RBAC: клиенты доступны (выдано право)", r.status_code == 200)

print()
if failures:
    print("ПРОВАЛЕНО:", ", ".join(failures))
    sys.exit(1)
print("Все проверки пройдены")
