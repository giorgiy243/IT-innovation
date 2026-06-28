"""Тесты write-операций вендоров: POST, PATCH, DELETE (Фаза 1.6.4).

Матрица прав:
  CREATE / DELETE : маркетолог, аналитик
  PATCH all fields: маркетолог, аналитик
  PATCH contacts  : РОП (только vendor_contact, deal_registration)
  READ only       : МОП, пресейл-инженер
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.db import get_db
from core.models import Base, Module, Role, RoleModule, Tenant, User, UserRole, Vendor
from core.rbac.routes import router as rbac_router
from core.vendors.routes import router as vendors_router

os.environ.setdefault("VENDOR_CRYPTO_KEY", "-d65RKqOJ56FVBRLACItmJu-YUKHGLlKM6NWAPHoSII=")

PW = "test-password-123"

# --- Тест-приложение ---

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(rbac_router)
test_app.include_router(vendors_router)


# --- Фикстуры ---

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


# --- Хелперы ---

def _seed_tenant(s, name="АйТек") -> Tenant:
    t = Tenant(name=name)
    s.add(t)
    s.flush()
    return t


def _seed_user(s, tenant: Tenant, login: str, role_code: str, role_name: str,
               module_codes: list[str]) -> User:
    """Пользователь с заданной ролью и набором модулей."""
    u = User(tenant_id=tenant.id, login=login, password_hash=hash_password(PW))
    s.add(u)
    s.flush()

    for code in module_codes:
        mod = Module(code=code, name=code.capitalize(), is_enabled=True, sort_order=10)
        s.merge(mod)
    s.flush()

    role = Role(tenant_id=tenant.id, code=role_code, name=role_name)
    s.add(role)
    s.flush()

    for code in module_codes:
        s.add(RoleModule(role_id=role.id, module_code=code))
    s.add(UserRole(user_id=u.id, role_id=role.id, scope="own"))
    s.flush()
    return u


def _seed_marketer(s, t, login="marketer"):
    return _seed_user(s, t, login, "marketer", "Маркетолог", ["sales", "marketing"])

def _seed_rop(s, t, login="rop"):
    return _seed_user(s, t, login, "rop", "РОП", ["sales"])

def _seed_mop(s, t, login="mop"):
    return _seed_user(s, t, login, "mop", "МОП", ["sales"])

def _seed_presale(s, t, login="presale"):
    return _seed_user(s, t, login, "presale_engineer", "Пресейл", ["sales"])

def _seed_analyst(s, t, login="analyst"):
    return _seed_user(s, t, login, "analyst", "Аналитик", ["sales"])


def _make_vendor(s, tenant_id, name="Cisco", **kw):
    v = Vendor(tenant_id=tenant_id, name=name, **kw)
    s.add(v)
    s.flush()
    return v


def _login(client, login):
    r = client.post("/login", json={"login": login, "password": PW})
    assert r.status_code == 200, r.text
    return r


# ===================== POST /api/vendors =====================

class TestCreate:
    def test_401_no_session(self, client):
        assert client.post("/api/vendors", json={"name": "X"}).status_code == 401

    def test_marketer_creates_201(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        r = client.post("/api/vendors", json={"name": "Cisco", "categories": "Сетевое"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Cisco"
        assert data["categories"] == "Сетевое"

    def test_analyst_creates_201(self, client, db):
        t = _seed_tenant(db)
        _seed_analyst(db, t)
        db.commit()
        _login(client, "analyst")
        r = client.post("/api/vendors", json={"name": "Fortinet"})
        assert r.status_code == 201
        assert r.json()["name"] == "Fortinet"

    def test_mop_cannot_create_403(self, client, db):
        t = _seed_tenant(db)
        _seed_mop(db, t)
        db.commit()
        _login(client, "mop")
        assert client.post("/api/vendors", json={"name": "X"}).status_code == 403

    def test_rop_cannot_create_403(self, client, db):
        t = _seed_tenant(db)
        _seed_rop(db, t)
        db.commit()
        _login(client, "rop")
        assert client.post("/api/vendors", json={"name": "X"}).status_code == 403

    def test_presale_cannot_create_403(self, client, db):
        t = _seed_tenant(db)
        _seed_presale(db, t)
        db.commit()
        _login(client, "presale")
        assert client.post("/api/vendors", json={"name": "X"}).status_code == 403

    def test_missing_name_422(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        assert client.post("/api/vendors", json={"categories": "ИБ"}).status_code == 422

    def test_duplicate_name_409(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        _make_vendor(db, t.id, "Cisco")
        db.commit()
        _login(client, "marketer")
        assert client.post("/api/vendors", json={"name": "Cisco"}).status_code == 409

    def test_password_encrypted_in_db(self, client, db):
        from core.vendors.crypto import decrypt
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        r = client.post("/api/vendors", json={"name": "Cisco", "portal_password": "secret"})
        assert r.status_code == 201
        assert r.json()["portal_password"] == "secret"
        v = db.query(Vendor).filter_by(tenant_id=t.id, name="Cisco").first()
        assert v.portal_password_enc is not None
        assert decrypt(v.portal_password_enc) == "secret"

    def test_tenant_isolation_on_create(self, client, db):
        t1 = _seed_tenant(db, "T1")
        t2 = _seed_tenant(db, "T2")
        _seed_marketer(db, t1, "m1")
        _seed_marketer(db, t2, "m2")
        db.commit()
        _login(client, "m1")
        client.post("/api/vendors", json={"name": "Cisco"})
        _login(client, "m2")
        r = client.post("/api/vendors", json={"name": "Cisco"})
        assert r.status_code == 201  # разные тенанты — не дубль


# ===================== PATCH /api/vendors/{id} =====================

class TestUpdate:
    def test_401_no_session(self, client, db):
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id)
        db.commit()
        assert client.patch(f"/api/vendors/{v.id}", json={"name": "Y"}).status_code == 401

    def test_marketer_updates_all_fields(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id, "Cisco")
        db.commit()
        _login(client, "marketer")
        r = client.patch(f"/api/vendors/{v.id}", json={
            "name": "Cisco Systems",
            "categories": "Сетевое",
            "vendor_contact": "Иванов",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Cisco Systems"
        assert data["categories"] == "Сетевое"
        assert data["vendor_contact"] == "Иванов"

    def test_analyst_updates_all_fields(self, client, db):
        t = _seed_tenant(db)
        _seed_analyst(db, t)
        v = _make_vendor(db, t.id, "HPE")
        db.commit()
        _login(client, "analyst")
        r = client.patch(f"/api/vendors/{v.id}", json={"name": "HPE Aruba", "discount": "20%"})
        assert r.status_code == 200
        assert r.json()["name"] == "HPE Aruba"

    def test_rop_updates_contacts(self, client, db):
        t = _seed_tenant(db)
        _seed_rop(db, t)
        v = _make_vendor(db, t.id, "Cisco")
        db.commit()
        _login(client, "rop")
        r = client.patch(f"/api/vendors/{v.id}", json={
            "vendor_contact": "Петров А.",
            "deal_registration": "portal.cisco.com/deals",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["vendor_contact"] == "Петров А."
        assert data["deal_registration"] == "portal.cisco.com/deals"

    def test_rop_cannot_change_name(self, client, db):
        t = _seed_tenant(db)
        _seed_rop(db, t)
        v = _make_vendor(db, t.id, "Cisco")
        db.commit()
        _login(client, "rop")
        r = client.patch(f"/api/vendors/{v.id}", json={
            "name": "Cisco Systems",
            "vendor_contact": "Петров",
        })
        assert r.status_code == 200
        assert r.json()["name"] == "Cisco"  # name не изменился
        assert r.json()["vendor_contact"] == "Петров"  # это изменилось

    def test_mop_cannot_update_403(self, client, db):
        t = _seed_tenant(db)
        _seed_mop(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "mop")
        assert client.patch(f"/api/vendors/{v.id}", json={"vendor_contact": "X"}).status_code == 403

    def test_presale_cannot_update_403(self, client, db):
        t = _seed_tenant(db)
        _seed_presale(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "presale")
        assert client.patch(f"/api/vendors/{v.id}", json={"vendor_contact": "X"}).status_code == 403

    def test_update_not_found_404(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        assert client.patch("/api/vendors/9999", json={"name": "X"}).status_code == 404

    def test_update_cross_tenant_404(self, client, db):
        t1 = _seed_tenant(db, "T1")
        t2 = _seed_tenant(db, "T2")
        _seed_marketer(db, t2, "m2")
        v = _make_vendor(db, t1.id, "Cisco")
        db.commit()
        _login(client, "m2")
        assert client.patch(f"/api/vendors/{v.id}", json={"name": "X"}).status_code == 404

    def test_update_password_re_encrypted(self, client, db):
        from core.vendors.crypto import decrypt
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "marketer")
        r = client.patch(f"/api/vendors/{v.id}", json={"portal_password": "newpwd"})
        assert r.status_code == 200
        assert r.json()["portal_password"] == "newpwd"
        db.expire(v)
        v2 = db.get(Vendor, v.id)
        assert decrypt(v2.portal_password_enc) == "newpwd"

    def test_duplicate_name_409(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        _make_vendor(db, t.id, "Cisco")
        v2 = _make_vendor(db, t.id, "HPE")
        db.commit()
        _login(client, "marketer")
        assert client.patch(f"/api/vendors/{v2.id}", json={"name": "Cisco"}).status_code == 409


# ===================== DELETE /api/vendors/{id} =====================

class TestDelete:
    def test_401_no_session(self, client, db):
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id)
        db.commit()
        assert client.delete(f"/api/vendors/{v.id}").status_code == 401

    def test_marketer_deletes_204(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        v = _make_vendor(db, t.id)
        vid = v.id
        db.commit()
        _login(client, "marketer")
        r = client.delete(f"/api/vendors/{vid}")
        assert r.status_code == 204
        db.expire_all()
        assert db.get(Vendor, vid) is None

    def test_analyst_deletes_204(self, client, db):
        t = _seed_tenant(db)
        _seed_analyst(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "analyst")
        assert client.delete(f"/api/vendors/{v.id}").status_code == 204

    def test_mop_cannot_delete_403(self, client, db):
        t = _seed_tenant(db)
        _seed_mop(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "mop")
        assert client.delete(f"/api/vendors/{v.id}").status_code == 403

    def test_rop_cannot_delete_403(self, client, db):
        t = _seed_tenant(db)
        _seed_rop(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "rop")
        assert client.delete(f"/api/vendors/{v.id}").status_code == 403

    def test_presale_cannot_delete_403(self, client, db):
        t = _seed_tenant(db)
        _seed_presale(db, t)
        v = _make_vendor(db, t.id)
        db.commit()
        _login(client, "presale")
        assert client.delete(f"/api/vendors/{v.id}").status_code == 403

    def test_delete_not_found_404(self, client, db):
        t = _seed_tenant(db)
        _seed_marketer(db, t)
        db.commit()
        _login(client, "marketer")
        assert client.delete("/api/vendors/9999").status_code == 404

    def test_delete_cross_tenant_404(self, client, db):
        t1 = _seed_tenant(db, "T1")
        t2 = _seed_tenant(db, "T2")
        _seed_marketer(db, t2, "m2")
        v = _make_vendor(db, t1.id)
        db.commit()
        _login(client, "m2")
        assert client.delete(f"/api/vendors/{v.id}").status_code == 404
