"""Smoke-проверка админки: вход, CRUD, расписание, слоты, двойная бронь.

Запуск: python scripts/smoke_admin.py  (требует поднятые postgres/redis)
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.services.schedule_service import local_now

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

# 8. Слоты на завтра (рабочий день пн-пт; берём ближайший будний день)
day = local_now().date() + timedelta(days=1)
while day.weekday() >= 5:
    day += timedelta(days=1)
r = c.get(f"/admin/bookings/slots?employee_id=1&service_id=1&date={day.isoformat()}")
slots_ok = r.status_code == 200 and "09:00" in r.text
check("слоты рассчитаны", slots_ok, f"({day} doweek={day.weekday()})")

# 9. Создание записи на первый свободный слот
import re

m = re.search(r'<option value="(\d\d:\d\d)">', r.text)
slot = m.group(1) if m else "10:00"
r = c.post(
    "/admin/bookings/create",
    data={
        "employee_id": "1", "service_id": "1", "date": day.isoformat(),
        "slot": slot, "client_id": "0", "client_name": "Пётр Тестовый",
        "client_phone": "+7 900 000-00-02", "notes": "smoke test",
    },
)
check("создание записи из админки", r.status_code == 303 and "err" not in r.headers.get("location", ""))

# 10. Двойная бронь на тот же слот → отказ
r = c.post(
    "/admin/bookings/create",
    data={
        "employee_id": "1", "service_id": "1", "date": day.isoformat(),
        "slot": slot, "client_id": "0", "client_name": "Дублёр", "client_phone": "",
    },
)
loc = r.headers.get("location", "")
check("двойная бронь отклонена", "err" in loc, loc[-60:])

# 11. Список записей содержит бронь
r = c.get(f"/admin/bookings?date={day.isoformat()}")
check("запись видна в списке", "Пётр Тестовый" in r.text)

# 12. Настройки и аудит
r = c.get("/admin/settings")
check("GET /admin/settings", r.status_code == 200)
r = c.get("/admin/audit")
check("GET /admin/audit", r.status_code == 200 and "booking.create" in r.text)

# 13. RBAC: сотрудник без права view_audit получает 403
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
print("Все проверки пройдены ✔")
