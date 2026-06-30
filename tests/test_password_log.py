"""Тесты журнала смены паролей (беклог: история смены паролей).

Что проверяем:
- create_user → запись event=initial, актор = админ; password_changed_at выставлен;
- grant_access → запись event=initial;
- /change-password → запись event=self_change, актор = сам пользователь, дата обновлена;
- GET /api/admin/users/{id}/password-history: security видит, остальные 403;
- значение пароля никогда не попадает в журнал.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.admin.routes import router as admin_router
from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.db import get_db
from core.models import (
    Base, Employee, Module, PasswordChangeLog, Role, RoleModule,
    Tenant, User, UserRole,
)
from core.rbac.routes import router as rbac_router

PW = "test-password-123"
NEW_PW = "Nieuwpass1!"  # проходит политику: верх/низ/спец/≥8

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(rbac_router)
test_app.include_router(admin_router)


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


def _seed_user(s, tenant, login, role_code, role_name, modules):
    u = User(tenant_id=tenant.id, login=login, password_hash=hash_password(PW))
    s.add(u); s.flush()
    for code in modules:
        s.merge(Module(code=code, name=code.capitalize(), is_enabled=True, sort_order=10))
    s.flush()
    role = s.execute(
        select(Role).where(Role.tenant_id == tenant.id, Role.code == role_code)
    ).scalar_one_or_none()
    if role is None:
        role = Role(tenant_id=tenant.id, code=role_code, name=role_name)
        s.add(role); s.flush()
        for code in modules:
            s.add(RoleModule(role_id=role.id, module_code=code))
    s.add(UserRole(user_id=u.id, role_id=role.id, scope="own"))
    s.flush()
    return u


def _seed_security(s, t, login="sec"):
    return _seed_user(s, t, login, "security", "ИБ", [])


def _seed_mop(s, t, login="mop"):
    return _seed_user(s, t, login, "mop", "МОП", ["sales"])


def _login(client, login, password=PW):
    r = client.post("/login", json={"login": login, "password": password})
    assert r.status_code == 200
    return r


def _logs(db, user_id):
    return db.query(PasswordChangeLog).filter_by(user_id=user_id).all()


def test_create_user_logs_initial(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed_security(db, t); db.commit()
    _login(client, "sec")

    r = client.post("/api/admin/users", json={"login": "newbie", "password": "Passw0rd!"})
    assert r.status_code == 201
    uid = r.json()["id"]
    db.expire_all()

    rows = _logs(db, uid)
    assert len(rows) == 1
    assert rows[0].event == "initial"
    assert rows[0].user_login == "newbie"
    assert rows[0].actor_login == "sec"
    # дата последней смены выставлена
    assert db.get(User, uid).password_changed_at is not None


def test_grant_access_logs_initial(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed_security(db, t)
    emp = Employee(
        tenant_id=t.id, last_name="Иванов", first_name="Иван",
        domain_name="ivanov", phone_extension="1234", is_active=True,
    )
    db.add(emp); db.commit()
    _login(client, "sec")

    r = client.post(f"/api/admin/employees/{emp.id}/grant-access")
    assert r.status_code == 200
    db.expire_all()

    user = db.execute(select(User).where(User.login == "ivanov")).scalar_one()
    rows = _logs(db, user.id)
    assert len(rows) == 1
    assert rows[0].event == "initial"
    assert rows[0].actor_login == "sec"
    assert user.password_changed_at is not None


def test_self_change_logs_and_sets_date(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    u = _seed_mop(db, t, "ivanov"); db.commit()
    _login(client, "ivanov")

    r = client.post("/change-password", json={"new_password": NEW_PW})
    assert r.status_code == 204
    db.expire_all()

    rows = _logs(db, u.id)
    assert len(rows) == 1
    assert rows[0].event == "self_change"
    assert rows[0].actor_login == "ivanov"  # сам себе актор
    assert db.get(User, u.id).password_changed_at is not None


def test_history_endpoint_security_only(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed_security(db, t)
    mop = _seed_mop(db, t, "ivanov"); db.commit()

    # security видит историю
    _login(client, "sec")
    r = client.get(f"/api/admin/users/{mop.id}/password-history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # mop не имеет доступа
    _login(client, "ivanov")
    assert client.get(f"/api/admin/users/{mop.id}/password-history").status_code == 403


def test_history_returns_entry_after_self_change(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    _seed_security(db, t)
    mop = _seed_mop(db, t, "ivanov"); db.commit()

    _login(client, "ivanov")
    client.post("/change-password", json={"new_password": NEW_PW})

    _login(client, "sec")
    r = client.get(f"/api/admin/users/{mop.id}/password-history")
    assert r.status_code == 200
    entry = r.json()[0]
    assert entry["event"] == "self_change"
    assert entry["event_label"] == "Смена пользователем"
    assert entry["actor_login"] == "ivanov"
    assert "created_at" in entry


def test_password_value_never_in_log(client, db):
    t = Tenant(name="T"); db.add(t); db.flush()
    u = _seed_mop(db, t, "ivanov"); db.commit()
    _login(client, "ivanov")
    client.post("/change-password", json={"new_password": NEW_PW})
    db.expire_all()

    for row in _logs(db, u.id):
        blob = " ".join(
            str(getattr(row, col)) for col in
            ("user_login", "actor_login", "event")
        )
        assert NEW_PW not in blob
