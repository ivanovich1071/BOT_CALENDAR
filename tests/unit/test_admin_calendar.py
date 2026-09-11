"""Админка: страница календаря, области видимости и переход из ячейки к новой записи."""

from urllib.parse import quote

from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.bot import services as bot
from app.db.database import get_db
from app.main import app
from app.models.employee import Employee
from app.models.user import User
from app.services import calendar_grid
from app.services.schedule_service import local_now


def test_все_виды_открываются(admin_client, db, employee, service, client, workday):
    card = bot.create(db, client_id=client.id, employee_id=employee.id, service_id=service.id, day=workday, slot="10:00")
    for view in ("day", "week", "week_all", "month"):
        page = admin_client.get(f"/admin/calendar?view={view}&date={workday.isoformat()}")
        assert page.status_code == 200, view
        # В месяце имён нет — только число записей в дне
        assert ("1 запись" if view == "month" else "Иванов") in page.text, view
    day = admin_client.get(f"/admin/calendar?view=day&date={workday.isoformat()}").text
    assert f"/admin/bookings/{card['id']}?back=" in day and "Пётр" in day
    assert f"/admin/bookings/new?employee_id={employee.id}&date={workday.isoformat()}&slot=11:00" in day


def test_по_умолчанию_день_всех(admin_client, employee):
    page = admin_client.get("/admin/calendar")
    assert page.status_code == 200
    assert calendar_grid.day_title(local_now().date()) in page.text


def test_сотрудник_видит_только_себя(db, employee, workday):
    db.add(Employee(name="Сидоров"))
    staff = User(login="ivanov", password_hash="x", role="employee", permissions=[], is_active=True)
    db.add(staff)
    db.commit()
    employee.user_id = staff.id
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: staff
    app.dependency_overrides[get_db] = lambda: db
    try:
        page = TestClient(app).get(f"/admin/calendar?view=day&date={workday.isoformat()}")
        assert page.status_code == 200
        assert "Иванов" in page.text and "Сидоров" not in page.text
        # Своя неделя — вид по умолчанию для сотрудника
        monday = calendar_grid.monday_of(local_now().date())
        assert calendar_grid.week_title(monday) in TestClient(app).get("/admin/calendar").text
    finally:
        app.dependency_overrides.clear()


def test_ячейка_открывает_форму_с_датой_и_временем(admin_client, employee, service, workday):
    back = quote(f"/admin/calendar?view=day&date={workday.isoformat()}")
    form = admin_client.get(
        f"/admin/bookings/new?employee_id={employee.id}&date={workday.isoformat()}&slot=11:00&back={back}"
    ).text
    assert f'value="{workday.isoformat()}"' in form
    assert 'name="selected" value="11:00"' in form and 'hx-trigger="load"' in form
    slots = admin_client.get(
        f"/admin/bookings/slots?employee_id={employee.id}&service_id={service.id}&date={workday.isoformat()}&selected=11:00"
    ).text
    assert '<option value="11:00" selected>' in slots
    # Часовая услуга с 12:30 упирается в перерыв 13–14
    late = admin_client.get(
        f"/admin/bookings/slots?employee_id={employee.id}&service_id={service.id}&date={workday.isoformat()}&selected=12:30"
    ).text
    assert "не помещается" in late


def test_после_создания_возврат_в_календарь_только_внутри_админки(admin_client, employee, service, client, workday):
    data = {
        "employee_id": str(employee.id),
        "service_id": str(service.id),
        "client_id": str(client.id),
        "date": workday.isoformat(),
        "slot": "11:00",
        "back": f"/admin/calendar?view=day&date={workday.isoformat()}",
    }
    response = admin_client.post("/admin/bookings/create", data=data, follow_redirects=False)
    assert response.headers["location"].startswith("/admin/calendar?view=day") and "ok=" in response.headers["location"]

    data.update(slot="15:00", back="https://example.com/admin/")
    response = admin_client.post("/admin/bookings/create", data=data, follow_redirects=False)
    assert response.headers["location"].startswith("/admin/bookings?date=")
