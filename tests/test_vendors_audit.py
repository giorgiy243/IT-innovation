"""Тесты аудит-лога вендоров (Фаза 1.6.4).

Что проверяем:
- create → одна запись action=create
- update → записи только по изменившимся полям; пароль не раскрывается
- delete → одна запись action=delete
- GET /{id}/audit: маркетолог видит только свои, аналитик — все, остальные 403
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sqlalchemy import select

from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.db import get_db
from core.models import (
    Base, Module, Role, RoleModule, Tenant, User, UserRole, Vendor,
    VendorAuditLog,
)
from core.rbac.routes import router as rbac_router
from core.vendors.routes import router as vendors_router

os.environ.setdefault("VENDOR_CRYPTO_KEY", "-d65RKqOJ56FVBRLACItmJu-YUKHGLlKM6NWAPHoSII=")

PW = "test-password-123"

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(rbac_router)
test_app.include_router(vendors_router)


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
        mod = Module(code=code, name=code.capitalize(), is_enabled=True, sort_order=10)
        s.merge(mod)
    s.flush()
    # get_or_create роли — несколько пользователей могут делить одну роль в тенанте
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


def _seed_marketer(s, t, login="marketer"):
    return _seed_user(s, t, login, "marketer", "Маркетолог", ["sales", "marketing"])

def _seed_analyst(s, t, login="analyst"):
    return _seed_user(s, t, login, "analyst", "Аналитик", ["sales"])

def _seed_mop(s, t, login="mop"):
    return _seed_user(s, t, login, "mop", "МОП", ["sales"])


def _make_vendor(s, tenant_id, name="Cisco", **kw):
    v = Vendor(tenant_id=tenant_id, name=name, **kw)
    s.add(v); s.flush()
    return v


def _login(client, login):
    r = client.post("/login", json={"login": login, "password": PW})
    assert r.status_code == 200
    return r


def _audit_rows(db, vendor_id):
    return db.query(VendorAuditLog).filter_by(vendor_id=vendor_id).all()


class TestAuditCreate:
    def test_create_writes_one_row(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t); db.commit()
        _login(client, "marketer")
        r = client.post("/api/vendors", json={"name": "Cisco"})
        assert r.status_code == 201
        vid = r.json()["id"]
        db.expire_all()
        rows = _audit_rows(db, vid)
        assert len(rows) == 1
        assert rows[0].action == "create"
        assert rows[0].user_login == "marketer"

    def test_create_audit_has_correct_vendor_name(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t); db.commit()
        _login(client, "marketer")
        r = client.post("/api/vendors", json={"name": "Fortinet"})
        assert r.status_code == 201
        vid = r.json()["id"]
        db.expire_all()
        rows = _audit_rows(db, vid)
        assert rows[0].vendor_name == "Fortinet"


class TestAuditUpdate:
    def test_update_writes_changed_fields_only(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id, "Cisco", categories="Сетевое")
        db.commit()
        _login(client, "marketer")
        client.patch(f"/api/vendors/{v.id}", json={
            "categories": "Сетевое, ИБ",
            "status_type": "active",
        })
        db.expire_all()
        rows = _audit_rows(db, v.id)
        fields = {r.field_name for r in rows if r.action == "update"}
        assert fields == {"categories", "status_type"}

    def test_update_unchanged_field_not_logged(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id, "Cisco", categories="Сетевое")
        db.commit()
        _login(client, "marketer")
        # отправляем то же значение categories
        client.patch(f"/api/vendors/{v.id}", json={"categories": "Сетевое"})
        db.expire_all()
        rows = [r for r in _audit_rows(db, v.id) if r.action == "update"]
        assert rows == []

    def test_update_password_not_exposed_in_audit(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "marketer")
        client.patch(f"/api/vendors/{v.id}", json={"portal_password": "secret123"})
        db.expire_all()
        row = next(r for r in _audit_rows(db, v.id) if r.field_name == "portal_password")
        assert "secret123" not in (row.old_value or "")
        assert "secret123" not in (row.new_value or "")
        assert row.new_value == "[задан]"

    def test_update_audit_records_old_and_new(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id, status_type="active")
        db.commit()
        _login(client, "marketer")
        client.patch(f"/api/vendors/{v.id}", json={"status_type": "suspended"})
        db.expire_all()
        row = next(r for r in _audit_rows(db, v.id) if r.field_name == "status_type")
        assert row.old_value == "active"
        assert row.new_value == "suspended"

    def test_rop_update_only_logs_allowed_fields(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_user(db, t, "rop", "rop", "РОП", ["sales"])
        v = _make_vendor(db, t.id, "Cisco")
        db.commit()
        _login(client, "rop")
        client.patch(f"/api/vendors/{v.id}", json={
            "vendor_contact": "Иванов",
            "name": "Cisco Systems",  # не разрешено — не должно логироваться
        })
        db.expire_all()
        fields = {r.field_name for r in _audit_rows(db, v.id) if r.action == "update"}
        assert "vendor_contact" in fields
        assert "name" not in fields


class TestAuditDelete:
    def test_delete_writes_one_row(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id, "Cisco")
        vid = v.id
        db.commit()
        _login(client, "marketer")
        client.delete(f"/api/vendors/{vid}")
        db.expire_all()
        row = db.query(VendorAuditLog).filter_by(vendor_name="Cisco", action="delete").first()
        assert row is not None
        assert row.user_login == "marketer"


class TestAuditEndpoint:
    def test_analyst_sees_all(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t, "m1")
        _seed_analyst(db, t)
        db.commit()
        _login(client, "m1")
        r = client.post("/api/vendors", json={"name": "Cisco"})
        vid = r.json()["id"]
        _login(client, "analyst")
        r = client.get(f"/api/vendors/{vid}/audit")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_marketer_sees_only_own(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_marketer(db, t, "m1")
        _seed_marketer(db, t, "m2")
        db.commit()
        _login(client, "m1")
        r = client.post("/api/vendors", json={"name": "Cisco"})
        vid = r.json()["id"]
        _login(client, "m2")
        client.patch(f"/api/vendors/{vid}", json={"categories": "Сетевое"})
        _login(client, "m1")
        r = client.get(f"/api/vendors/{vid}/audit")
        assert r.status_code == 200
        logins = {e["user_login"] for e in r.json()}
        assert logins == {"m1"}

    def test_mop_gets_403(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_mop(db, t)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        r = client.post("/api/vendors", json={"name": "Cisco"})
        vid = r.json()["id"]
        _login(client, "mop")
        assert client.get(f"/api/vendors/{vid}/audit").status_code == 403

    def test_audit_entry_has_required_fields(self, client, db):
        t = Tenant(name="T"); db.add(t); db.flush()
        _seed_analyst(db, t)
        db.commit()
        _login(client, "analyst")
        r = client.post("/api/vendors", json={"name": "HPE"})
        vid = r.json()["id"]
        r = client.get(f"/api/vendors/{vid}/audit")
        entry = r.json()[0]
        assert "action" in entry
        assert "user_login" in entry
        assert "created_at" in entry
