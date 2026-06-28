"""Тесты сервисного слоя и API-маршрутов вендоров (Фаза 1.6.3).

SQLite in-memory (StaticPool) - изоляция от prod-БД.
Тестирует: list_vendors, get_vendor, unique_categories, vendor_to_detail,
           API GET /api/vendors (фильтры, tenant-изоляция, 401/403),
           API GET /api/vendors/{id} (карточка, 404, cross-tenant).
"""
from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.db import get_db
from core.models import (
    Base,
    Module,
    Role,
    RoleModule,
    Tenant,
    User,
    UserRole,
    Vendor,
    VendorDistributor,
)
from core.rbac.routes import router as rbac_router
from core.vendors.routes import router as vendors_router
from core.vendors.service import (
    get_vendor,
    list_vendors,
    unique_categories,
    vendor_to_detail,
    vendor_to_list_item,
)

os.environ.setdefault("VENDOR_CRYPTO_KEY", "-d65RKqOJ56FVBRLACItmJu-YUKHGLlKM6NWAPHoSII=")

PW = "test-password-123"


# --- Тест-приложение ---

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(rbac_router)
test_app.include_router(vendors_router)


# --- SQLite движок и сессия ---

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


# --- Вспомогательные функции ---

def _seed_tenant(s, name="АйТек") -> Tenant:
    t = Tenant(name=name)
    s.add(t)
    s.flush()
    return t


def _seed_user_with_sales(s, tenant: Tenant, login: str = "mop") -> User:
    """Пользователь с ролью mop и доступом к модулю sales."""
    u = User(tenant_id=tenant.id, login=login, password_hash=hash_password(PW))
    s.add(u)
    s.flush()

    mod = Module(code="sales", name="Продажи", is_enabled=True, sort_order=10)
    s.merge(mod)
    s.flush()

    role = Role(tenant_id=tenant.id, code="mop", name="МОП")
    s.add(role)
    s.flush()

    s.add(RoleModule(role_id=role.id, module_code="sales"))
    s.add(UserRole(user_id=u.id, role_id=role.id, scope="own"))
    s.flush()
    return u


def _make_vendor(s, tenant_id: int, name: str, **kw) -> Vendor:
    v = Vendor(tenant_id=tenant_id, name=name, **kw)
    s.add(v)
    s.flush()
    return v


# ===================== Тесты сервисного слоя =====================


class TestListVendors:
    def test_returns_own_tenant(self, db):
        t = _seed_tenant(db)
        _make_vendor(db, t.id, "Cisco", categories="Сетевое", status_type="active")
        _make_vendor(db, t.id, "Kaspersky", categories="ИБ", status_type="none")
        db.commit()

        result = list_vendors(db, t.id)
        assert len(result) == 2
        assert result[0].name == "Cisco"  # сортировка по имени

    def test_tenant_isolation(self, db):
        t1 = _seed_tenant(db, "Арендатор1")
        t2 = _seed_tenant(db, "Арендатор2")
        _make_vendor(db, t1.id, "Cisco")
        _make_vendor(db, t2.id, "Fortinet")
        db.commit()

        assert len(list_vendors(db, t1.id)) == 1
        assert list_vendors(db, t1.id)[0].name == "Cisco"
        assert len(list_vendors(db, t2.id)) == 1

    def test_filter_by_name(self, db):
        t = _seed_tenant(db)
        _make_vendor(db, t.id, "Cisco Systems")
        _make_vendor(db, t.id, "Kaspersky")
        db.commit()

        result = list_vendors(db, t.id, q="cisco")
        assert len(result) == 1
        assert result[0].name == "Cisco Systems"

    def test_filter_by_category(self, db):
        t = _seed_tenant(db)
        _make_vendor(db, t.id, "Cisco", categories="Сетевое, ИБ")
        _make_vendor(db, t.id, "Kaspersky", categories="ИБ")
        _make_vendor(db, t.id, "HPE", categories="Серверы")
        db.commit()

        result = list_vendors(db, t.id, category="ИБ")
        names = {v.name for v in result}
        assert names == {"Cisco", "Kaspersky"}

    def test_filter_by_status(self, db):
        t = _seed_tenant(db)
        _make_vendor(db, t.id, "Cisco", status_type="active")
        _make_vendor(db, t.id, "Kaspersky", status_type="suspended")
        db.commit()

        result = list_vendors(db, t.id, status_type="active")
        assert len(result) == 1
        assert result[0].name == "Cisco"

    def test_empty_tenant(self, db):
        t = _seed_tenant(db)
        db.commit()
        assert list_vendors(db, t.id) == []


class TestGetVendor:
    def test_found(self, db):
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id, "Cisco")
        db.commit()

        found = get_vendor(db, t.id, v.id)
        assert found is not None
        assert found.name == "Cisco"

    def test_not_found(self, db):
        t = _seed_tenant(db)
        db.commit()
        assert get_vendor(db, t.id, 9999) is None

    def test_cross_tenant_blocked(self, db):
        t1 = _seed_tenant(db, "Арендатор1")
        t2 = _seed_tenant(db, "Арендатор2")
        v = _make_vendor(db, t1.id, "Cisco")
        db.commit()

        assert get_vendor(db, t2.id, v.id) is None


class TestUniqueCategories:
    def test_splits_by_comma(self, db):
        t = _seed_tenant(db)
        _make_vendor(db, t.id, "A", categories="ИБ, Сетевое")
        _make_vendor(db, t.id, "B", categories="ИБ")
        _make_vendor(db, t.id, "C", categories=None)
        db.commit()

        cats = unique_categories(db, t.id)
        assert cats == ["ИБ", "Сетевое"]  # sorted, no duplicates

    def test_empty_if_no_vendors(self, db):
        t = _seed_tenant(db)
        db.commit()
        assert unique_categories(db, t.id) == []

    def test_tenant_isolation(self, db):
        t1 = _seed_tenant(db, "T1")
        t2 = _seed_tenant(db, "T2")
        _make_vendor(db, t1.id, "A", categories="ИБ")
        _make_vendor(db, t2.id, "B", categories="Серверы")
        db.commit()

        assert unique_categories(db, t1.id) == ["ИБ"]
        assert unique_categories(db, t2.id) == ["Серверы"]


class TestVendorToDetail:
    def test_password_decrypted(self, db):
        from core.vendors.crypto import encrypt

        t = _seed_tenant(db)
        enc = encrypt("secret123")
        v = _make_vendor(db, t.id, "Cisco", portal_password_enc=enc)
        db.commit()

        detail = vendor_to_detail(v)
        assert detail["portal_password"] == "secret123"

    def test_null_password(self, db):
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id, "Cisco", portal_password_enc=None)
        db.commit()

        detail = vendor_to_detail(v)
        assert detail["portal_password"] is None

    def test_distributors_included(self, db):
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id, "Cisco")
        db.add(VendorDistributor(vendor_id=v.id, sort_order=1, name="Дистрибьютор А"))
        db.commit()

        # Reload to trigger relationship load
        db.expire(v)
        detail = vendor_to_detail(db.get(Vendor, v.id))
        assert len(detail["distributors"]) == 1
        assert detail["distributors"][0]["name"] == "Дистрибьютор А"

    def test_valid_until_iso_format(self, db):
        from datetime import date
        t = _seed_tenant(db)
        v = _make_vendor(db, t.id, "Cisco", valid_until=date(2027, 6, 1))
        db.commit()

        detail = vendor_to_detail(v)
        assert detail["valid_until"] == "2027-06-01"


# ===================== Тесты API-маршрутов =====================


def _login(client, login="mop"):
    r = client.post("/login", json={"login": login, "password": PW})
    assert r.status_code == 200, r.text
    return r


class TestApiList:
    def test_401_no_session(self, client):
        r = client.get("/api/vendors")
        assert r.status_code == 401

    def test_403_no_sales_module(self, client, db):
        t = _seed_tenant(db)
        User_ = User(tenant_id=t.id, login="guest", password_hash=hash_password(PW))
        db.add(User_)
        db.commit()
        _login(client, "guest")
        r = client.get("/api/vendors")
        assert r.status_code == 403

    def test_returns_vendors(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        _make_vendor(db, t.id, "Cisco", categories="Сетевое", status_type="active")
        _make_vendor(db, t.id, "Kaspersky", categories="ИБ", status_type="none")
        db.commit()

        _login(client)
        r = client.get("/api/vendors")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        names = [v["name"] for v in data["vendors"]]
        assert "Cisco" in names and "Kaspersky" in names

    def test_categories_in_response(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        _make_vendor(db, t.id, "Cisco", categories="Сетевое, ИБ")
        db.commit()

        _login(client)
        r = client.get("/api/vendors")
        cats = r.json()["categories"]
        assert "ИБ" in cats and "Сетевое" in cats

    def test_filter_q(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        _make_vendor(db, t.id, "Cisco Systems")
        _make_vendor(db, t.id, "Kaspersky")
        db.commit()

        _login(client)
        r = client.get("/api/vendors?q=cisco")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["vendors"][0]["name"] == "Cisco Systems"

    def test_filter_status(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        _make_vendor(db, t.id, "Cisco", status_type="active")
        _make_vendor(db, t.id, "Kaspersky", status_type="suspended")
        db.commit()

        _login(client)
        r = client.get("/api/vendors?status_type=active")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["vendors"][0]["name"] == "Cisco"


class TestApiDetail:
    def test_401_no_session(self, client):
        r = client.get("/api/vendors/1")
        assert r.status_code == 401

    def test_404_not_found(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        db.commit()
        _login(client)
        r = client.get("/api/vendors/9999")
        assert r.status_code == 404

    def test_returns_detail(self, client, db):
        from core.vendors.crypto import encrypt

        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        enc = encrypt("portal-pwd")
        v = _make_vendor(
            db, t.id, "Cisco",
            categories="Сетевое",
            status_type="active",
            portal_login="admin",
            portal_password_enc=enc,
        )
        db.commit()

        _login(client)
        r = client.get(f"/api/vendors/{v.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Cisco"
        assert data["portal_login"] == "admin"
        assert data["portal_password"] == "portal-pwd"

    def test_cross_tenant_404(self, client, db):
        t1 = _seed_tenant(db, "Т1")
        t2 = _seed_tenant(db, "Т2")
        # mop в t2
        _seed_user_with_sales(db, t2, login="mop2")
        v = _make_vendor(db, t1.id, "Cisco")
        db.commit()

        _login(client, "mop2")
        r = client.get(f"/api/vendors/{v.id}")
        assert r.status_code == 404

    def test_distributor_in_detail(self, client, db):
        t = _seed_tenant(db)
        _seed_user_with_sales(db, t)
        v = _make_vendor(db, t.id, "Cisco")
        db.add(VendorDistributor(vendor_id=v.id, sort_order=1, name="Ланит", email="lanit@example.com"))
        db.commit()

        _login(client)
        r = client.get(f"/api/vendors/{v.id}")
        assert r.status_code == 200
        dists = r.json()["distributors"]
        assert len(dists) == 1
        assert dists[0]["name"] == "Ланит"
        assert dists[0]["email"] == "lanit@example.com"
