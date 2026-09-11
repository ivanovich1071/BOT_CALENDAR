"""Карточка сотрудника: статус без логина и сброс пароля."""

from app.models.user import User


def test_галочка_активности_есть_у_сотрудника_без_логина(admin_client, employee):
    page = admin_client.get(f"/admin/employees/{employee.id}/edit")
    assert page.status_code == 200
    assert 'name="is_active"' in page.text


def test_сотрудник_без_логина_включается_и_выключается(admin_client, db, employee):
    employee.is_active = False
    db.commit()
    url = f"/admin/employees/{employee.id}/update"

    admin_client.post(url, data={"name": employee.name, "is_active": "on"}, follow_redirects=False)
    db.refresh(employee)
    assert employee.is_active is True

    admin_client.post(url, data={"name": employee.name}, follow_redirects=False)
    db.refresh(employee)
    assert employee.is_active is False


def test_сброс_пароля(admin_client, db, employee):
    staff = User(login="ivanov", password_hash="old", role="employee", permissions=[], is_active=True)
    db.add(staff)
    db.commit()
    employee.user_id = staff.id
    db.commit()

    page = admin_client.get(f"/admin/employees/{employee.id}/edit")
    assert 'name="password"' in page.text

    response = admin_client.post(
        f"/admin/employees/{employee.id}/reset-password",
        data={"password": "новый-пароль-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    db.refresh(staff)
    assert staff.password_hash != "old"
