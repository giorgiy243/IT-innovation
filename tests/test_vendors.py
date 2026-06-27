"""Тесты модуля вендоров (Фаза 1.6.1): модели + Fernet-шифрование.

SQLite in-memory, не трогает рабочую БД.
"""
from __future__ import annotations

import os
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import Base, Tenant, Vendor, VendorDistributor
from core.vendors.crypto import decrypt, encrypt

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_crypto_key(monkeypatch):
    monkeypatch.setenv("VENDOR_CRYPTO_KEY", TEST_KEY)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    tenant = Tenant(name="АйТек")
    s.add(tenant)
    s.commit()
    try:
        yield s, tenant
    finally:
        s.close()
        engine.dispose()


# --- Fernet crypto ---

def test_encrypt_decrypt_roundtrip():
    token = encrypt("secret123")
    assert token != "secret123"
    assert decrypt(token) == "secret123"


def test_encrypt_empty_string():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_encrypt_produces_different_tokens():
    # Fernet использует случайный IV — два шифрования одного текста дают разные токены
    t1 = encrypt("same")
    t2 = encrypt("same")
    assert t1 != t2
    assert decrypt(t1) == decrypt(t2) == "same"


def test_decrypt_wrong_key(monkeypatch):
    from cryptography.fernet import InvalidToken
    token = encrypt("secret")
    monkeypatch.setenv("VENDOR_CRYPTO_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        decrypt(token)


def test_crypto_key_missing(monkeypatch):
    monkeypatch.delenv("VENDOR_CRYPTO_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VENDOR_CRYPTO_KEY"):
        encrypt("anything")


# --- Vendor model ---

def test_vendor_created(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Код безопасности", categories="ИБ",
               status_type="active", status_text="Gold Partner")
    s.add(v)
    s.commit()
    assert v.id is not None
    assert s.get(Vendor, v.id).name == "Код безопасности"


def test_vendor_unique_name_per_tenant(session):
    s, t = session
    s.add(Vendor(tenant_id=t.id, name="Дубль"))
    s.commit()
    s.add(Vendor(tenant_id=t.id, name="Дубль"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_vendor_same_name_different_tenants(session):
    s, t = session
    t2 = Tenant(name="Другой")
    s.add(t2)
    s.commit()
    s.add(Vendor(tenant_id=t.id, name="Общий вендор"))
    s.add(Vendor(tenant_id=t2.id, name="Общий вендор"))
    s.commit()


def test_vendor_portal_password_stored_encrypted(session):
    s, t = session
    raw = "MyPortalPass!"
    v = Vendor(tenant_id=t.id, name="Вендор с паролем",
               portal_login="admin", portal_password_enc=encrypt(raw))
    s.add(v)
    s.commit()
    loaded = s.get(Vendor, v.id)
    # В БД — не открытый текст
    assert loaded.portal_password_enc != raw
    # Но расшифровывается обратно
    assert decrypt(loaded.portal_password_enc) == raw


def test_vendor_status_none_allowed(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Вендор без статуса", status_type="none")
    s.add(v)
    s.commit()
    assert s.get(Vendor, v.id).status_type == "none"


def test_vendor_nullable_fields(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Минимальный вендор")
    s.add(v)
    s.commit()
    loaded = s.get(Vendor, v.id)
    assert loaded.status_type is None
    assert loaded.valid_until is None
    assert loaded.portal_password_enc is None


# --- VendorDistributor ---

def test_distributor_linked_to_vendor(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Вендор")
    s.add(v)
    s.commit()
    d = VendorDistributor(vendor_id=v.id, sort_order=1, name="Дистрибьютор А",
                          email="dist@a.ru", phone="+7-999-000-00-00")
    s.add(d)
    s.commit()
    assert len(v.distributors) == 1
    assert v.distributors[0].name == "Дистрибьютор А"


def test_vendor_cascade_deletes_distributors(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Вендор каскад")
    s.add(v)
    s.commit()
    s.add(VendorDistributor(vendor_id=v.id, sort_order=1, name="Дист 1"))
    s.add(VendorDistributor(vendor_id=v.id, sort_order=2, name="Дист 2"))
    s.commit()
    s.delete(v)
    s.commit()
    from sqlalchemy import select
    remaining = s.execute(select(VendorDistributor)).scalars().all()
    assert remaining == []


def test_distributors_ordered_by_sort_order(session):
    s, t = session
    v = Vendor(tenant_id=t.id, name="Вендор порядок")
    s.add(v)
    s.commit()
    s.add(VendorDistributor(vendor_id=v.id, sort_order=3, name="Третий"))
    s.add(VendorDistributor(vendor_id=v.id, sort_order=1, name="Первый"))
    s.add(VendorDistributor(vendor_id=v.id, sort_order=2, name="Второй"))
    s.commit()
    s.expire(v)
    names = [d.name for d in v.distributors]
    assert names == ["Первый", "Второй", "Третий"]
