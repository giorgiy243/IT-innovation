"""Тесты личного кабинета (/api/me): профиль и смена пароля из кабинета.

Что проверяем:
- GET /profile отдаёт логин/роли/поля Employee; без карточки has_employee=false;
- PATCH /profile меняет редактируемые поля; без карточки → 400; пустая фамилия → 400;
- POST /change-password: верный текущий → 204 + журнал self_change + дата + новый
  пароль работает; неверный текущий → 400; новый == старому → 400; слабый → 400.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.db import get_db
from core.me.routes import router as me_router
from core.models import (
    Base, Employee, PasswordChangeLog, Role, Tenant, User, UserRole,
)
from core.rbac.routes import router as rbac_router

PW = "test-password-123"
NEW_PW = "Nieuwpass1!"

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(rbac_router)
test_app.include_router(me_router)


@pytest.fixture()
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    test_app.dependency_overrides[get_db] = override
    with TestClient(test_app) as c:
        yield c
    test_app.dependency_overrides.clear()


def _seed(s, tenant, login="ivanov", with_employee=True, with_role=True):
    emp = None
    if with_employee:
        emp = Employee(
            tenant_id=tenant.id, last_name="Иванов", first_name="Иван",
            middle_name="Иванович", position="Менеджер", email="old@test.ru",
            phone_personal="+7 900 000-00-00", domain_name="ivanov",
            phone_extension="1234", is_active=True,
        )
        s.add(emp); s.flush()
    u = User(
        tenant_id=tenant.id, login=login, password_hash=hash_password(PW),
        employee_id=emp.id if emp else None,
    )
    s.add(u); s.flush()
    if with_role:
        role = Role(tenant_id=tenant.id, code="mop", name="МОП")
        s.add(role); s.flush()
        s.add(UserRole(user_id=u.id, role_id=role.id, scope="own"))
        s.flush()
    return u, emp


def _login(client, login="ivanov", password=PW):
    r = client.post("/login", json={"login": login, "password": password})
    assert r.status_code == 200
    return r


def test_get_profile_returns_fields(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t); db.commit()
    _login(client)

    r = client.get("/api/me/profile")
    assert r.status_code == 200
    p = r.json()
    assert p["login"] == "ivanov"
    assert p["has_employee"] is True
    assert p["full_name"] == "Иванов Иван Иванович"
    assert p["email"] == "old@test.ru"
    assert p["domain_name"] == "ivanov"
    assert "МОП" in p["roles"]


def test_get_profile_no_employee(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t, with_employee=False); db.commit()
    _login(client)

    r = client.get("/api/me/profile")
    assert r.status_code == 200
    p = r.json()
    assert p["has_employee"] is False
    assert p["full_name"] is None
    assert p["email"] is None


def test_patch_updates_fields(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    u, emp = _seed(db, t); db.commit()
    _login(client)

    r = client.patch("/api/me/profile", json={
        "email": "new@test.ru", "phone_personal": "+7 911 111-11-11",
    })
    assert r.status_code == 200
    assert r.json()["email"] == "new@test.ru"
    db.expire_all()
    assert db.get(Employee, emp.id).email == "new@test.ru"


def test_patch_empty_string_clears_optional(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    u, emp = _seed(db, t); db.commit()
    _login(client)

    r = client.patch("/api/me/profile", json={"email": ""})
    assert r.status_code == 200
    assert r.json()["email"] is None


def test_patch_without_employee_400(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t, with_employee=False); db.commit()
    _login(client)

    r = client.patch("/api/me/profile", json={"email": "x@test.ru"})
    assert r.status_code == 400


def test_patch_empty_last_name_400(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t); db.commit()
    _login(client)

    r = client.patch("/api/me/profile", json={"last_name": "   "})
    assert r.status_code == 400


def test_change_password_wrong_current_400(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t); db.commit()
    _login(client)

    r = client.post("/api/me/change-password", json={
        "current_password": "wrong-one", "new_password": NEW_PW,
    })
    assert r.status_code == 400


def test_change_password_success(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    u, _ = _seed(db, t); db.commit()
    _login(client)

    r = client.post("/api/me/change-password", json={
        "current_password": PW, "new_password": NEW_PW,
    })
    assert r.status_code == 204
    db.expire_all()

    # дата выставлена, журнал self_change записан
    assert db.get(User, u.id).password_changed_at is not None
    rows = db.query(PasswordChangeLog).filter_by(user_id=u.id).all()
    assert len(rows) == 1
    assert rows[0].event == "self_change"

    # новый пароль работает, старый — нет
    assert client.post("/login", json={"login": "ivanov", "password": NEW_PW}).status_code == 200
    assert client.post("/login", json={"login": "ivanov", "password": PW}).status_code == 401


def test_change_password_same_as_old_400(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t); db.commit()
    _login(client)

    r = client.post("/api/me/change-password", json={
        "current_password": PW, "new_password": PW,
    })
    assert r.status_code == 400


def test_change_password_weak_400(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed(db, t); db.commit()
    _login(client)

    r = client.post("/api/me/change-password", json={
        "current_password": PW, "new_password": "short",
    })
    assert r.status_code == 400


def test_profile_requires_auth(client, db):
    assert client.get("/api/me/profile").status_code == 401
