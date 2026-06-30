"""Тесты API модуля «Ротация клиентов» (Фаза 3).

SQLite in-memory (StaticPool) - изоляция от prod-БД. Покрывает:
- доступ: 401 без сессии, 403 без модуля, 200 для rop;
- scope: all видит всех, own фильтрует по менеджеру, own без employee - пусто;
- эффективный статус (override из assignments важнее исходного);
- GET /managers (активные, уволенные исключены);
- POST /assignments (создание/обновление, валидация company/employee/тела);
- tenant-изоляция.
"""
from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth.passwords import hash_password
from core.auth.routes import router as auth_router
from core.client_rotation.export import TEMPLATE_HEADERS
from core.client_rotation.routes import router as client_rotation_router
from core.db import get_db
from core.models import (
    Assignment,
    Base,
    ClientRotationData,
    Company,
    Employee,
    Module,
    Role,
    RoleModule,
    Summary,
    Tenant,
    User,
    UserRole,
)

PW = "test-password-123"

test_app = FastAPI()
test_app.include_router(auth_router)
test_app.include_router(client_rotation_router)


@pytest.fixture()
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


# --- helpers ---

def _tenant(s, name="АйТек") -> Tenant:
    t = Tenant(name=name)
    s.add(t)
    s.flush()
    return t


def _employee(s, tenant_id, last_name, crm_name, is_active=True, manager_id=None) -> Employee:
    e = Employee(
        tenant_id=tenant_id, last_name=last_name, crm_name=crm_name,
        is_active=is_active, manager_id=manager_id,
    )
    s.add(e)
    s.flush()
    return e


def _user(s, tenant, login, role_code, scope, module="client_rotation", employee_id=None) -> User:
    u = User(tenant_id=tenant.id, login=login, password_hash=hash_password(PW), employee_id=employee_id)
    s.add(u)
    s.flush()
    mod = Module(code=module, name=module, is_enabled=True, sort_order=15)
    s.merge(mod)
    s.flush()
    role = Role(tenant_id=tenant.id, code=role_code, name=role_code)
    s.add(role)
    s.flush()
    s.add(RoleModule(role_id=role.id, module_code=module))
    s.add(UserRole(user_id=u.id, role_id=role.id, scope=scope))
    s.flush()
    return u


def _company(s, tenant_id, name, source_key, inn=None) -> Company:
    c = Company(tenant_id=tenant_id, source_key=source_key, inn=inn, name=name)
    s.add(c)
    s.flush()
    return c


def _crd(s, tenant_id, company_id, current_manager=None, score=0, transfer_status=None, **extra) -> ClientRotationData:
    crd = ClientRotationData(
        tenant_id=tenant_id, company_id=company_id,
        current_manager=current_manager, score=score, transfer_status=transfer_status, **extra,
    )
    s.add(crd)
    s.flush()
    return crd


def _login(client, login):
    r = client.post("/login", json={"login": login, "password": PW})
    assert r.status_code == 200, r.text
    return r


# --- доступ ---

class TestAccess:
    def test_401_no_session(self, client):
        assert client.get("/api/client-rotation/clients").status_code == 401

    def test_403_no_module(self, client, db):
        t = _tenant(db)
        _user(db, t, "mopuser", "mop", "own", module="sales")  # есть sales, нет client_rotation
        db.commit()
        _login(client, "mopuser")
        assert client.get("/api/client-rotation/clients").status_code == 403

    def test_200_for_rop(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        db.commit()
        _login(client, "rop")
        r = client.get("/api/client-rotation/clients")
        assert r.status_code == 200
        assert r.json()["scope"] == "all"


# --- scope ---

class TestScope:
    def test_all_sees_every_client(self, client, db):
        t = _tenant(db)
        _user(db, t, "boss", "analyst", "all")
        c1 = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Бета", "7700000002", "7700000002")
        _crd(db, t.id, c1.id, current_manager="Иванов И.И.", score=10)
        _crd(db, t.id, c2.id, current_manager="Петров П.П.", score=20)
        db.commit()
        _login(client, "boss")
        data = client.get("/api/client-rotation/clients").json()
        assert data["total"] == 2
        # сортировка по score DESC
        assert data["clients"][0]["name"] == "Бета"

    def test_own_filters_by_manager(self, client, db):
        t = _tenant(db)
        emp = _employee(db, t.id, "Иванов", "Иванов И.И.")
        _user(db, t, "ivanov", "rop", "own", employee_id=emp.id)
        c1 = _company(db, t.id, "Мой клиент", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Чужой клиент", "7700000002", "7700000002")
        _crd(db, t.id, c1.id, current_manager="Иванов И.И.", score=10)
        _crd(db, t.id, c2.id, current_manager="Петров П.П.", score=20)
        db.commit()
        _login(client, "ivanov")
        data = client.get("/api/client-rotation/clients").json()
        assert data["total"] == 1
        assert data["clients"][0]["name"] == "Мой клиент"

    def test_own_without_employee_sees_nothing(self, client, db):
        t = _tenant(db)
        _user(db, t, "noemp", "rop", "own")  # employee_id=None
        c1 = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c1.id, current_manager="Иванов И.И.", score=10)
        db.commit()
        _login(client, "noemp")
        data = client.get("/api/client-rotation/clients").json()
        assert data["total"] == 0  # fail-closed

    def test_team_includes_subordinates(self, client, db):
        t = _tenant(db)
        boss = _employee(db, t.id, "Начальников", "Начальников Н.Н.")
        _employee(db, t.id, "Подчинённый", "Подчинённый П.П.", manager_id=boss.id)
        _user(db, t, "boss", "rop", "team", employee_id=boss.id)
        c1 = _company(db, t.id, "Свой", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Подчинённого", "7700000002", "7700000002")
        c3 = _company(db, t.id, "Чужой", "7700000003", "7700000003")
        _crd(db, t.id, c1.id, current_manager="Начальников Н.Н.")
        _crd(db, t.id, c2.id, current_manager="Подчинённый П.П.")
        _crd(db, t.id, c3.id, current_manager="Чужак Ч.Ч.")
        db.commit()
        _login(client, "boss")
        data = client.get("/api/client-rotation/clients").json()
        names = {c["name"] for c in data["clients"]}
        assert names == {"Свой", "Подчинённого"}


# --- эффективный статус ---

def test_effective_status_override(client, db):
    t = _tenant(db)
    _user(db, t, "rop", "rop", "all")
    c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
    _crd(db, t.id, c.id, current_manager="Иванов И.И.", score=5, transfer_status="исходный")
    db.add(Assignment(tenant_id=t.id, company_id=c.id, transfer_status="ручной"))
    db.commit()
    _login(client, "rop")
    data = client.get("/api/client-rotation/clients").json()
    assert data["clients"][0]["transfer_status"] == "ручной"  # override важнее


# --- менеджеры ---

def test_managers_excludes_inactive(client, db):
    t = _tenant(db)
    _user(db, t, "rop", "rop", "all")
    _employee(db, t.id, "Активный", "Активный А.А.", is_active=True)
    _employee(db, t.id, "Уволенный", "Уволенный У.У.", is_active=False)
    db.commit()
    _login(client, "rop")
    data = client.get("/api/client-rotation/managers").json()
    crms = {m["crm_name"] for m in data}
    assert "Активный А.А." in crms
    assert "Уволенный У.У." not in crms


# --- назначения (POST) ---

class TestAssignment:
    def test_create(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/assignments", json={
            "company_id": c.id, "assigned_to_employee_id": emp.id, "transfer_status": "передан",
        })
        assert r.status_code == 200
        assert r.json()["assigned_to_employee_id"] == emp.id

    def test_update_upsert(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "transfer_status": "первый"})
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "transfer_status": "второй"})
        # одна строка (upsert), не две
        assert db.query(Assignment).filter(Assignment.company_id == c.id).count() == 1
        assert db.query(Assignment).filter(Assignment.company_id == c.id).one().transfer_status == "второй"

    def test_missing_company_id_422(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/assignments", json={"transfer_status": "x"})
        assert r.status_code == 422

    def test_bool_company_id_rejected_422(self, client, db):
        # JSON true не должен пройти как id=1 (bool - подкласс int).
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/assignments", json={"company_id": True})
        assert r.status_code == 422

    def test_unknown_company_404(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/assignments", json={"company_id": 999})
        assert r.status_code == 404

    def test_unknown_employee_422(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/assignments", json={
            "company_id": c.id, "assigned_to_employee_id": 999,
        })
        assert r.status_code == 422


def test_tenant_isolation(client, db):
    t1 = _tenant(db, "Арендатор1")
    t2 = _tenant(db, "Арендатор2")
    _user(db, t1, "rop1", "rop", "all")
    c2 = _company(db, t2.id, "Чужой", "7700000099", "7700000099")
    _crd(db, t2.id, c2.id, current_manager="Чужак")
    db.commit()
    _login(client, "rop1")
    data = client.get("/api/client-rotation/clients").json()
    assert data["total"] == 0  # клиент другого арендатора не виден


# --- экспорт в 1С (xlsx) ---

def _xlsx_rows(content: bytes) -> list[tuple]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    return list(wb.active.iter_rows(values_only=True))


class TestExport:
    def test_returns_xlsx_with_assigned(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id, current_manager="Иванов И.И.", phone="111")
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        r = client.get("/api/client-rotation/export")
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        rows = _xlsx_rows(r.content)
        assert rows[0] == tuple(TEMPLATE_HEADERS)
        assert any(row[1] == "Альфа" and row[0] == "Принимающий П.П." for row in rows[1:])

    def test_only_assigned_exported(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "БезМенеджера", "7700000002", "7700000002")
        _crd(db, t.id, c.id)
        db.add(Assignment(tenant_id=t.id, company_id=c.id, transfer_status="свой"))  # без менеджера
        db.commit()
        _login(client, "rop")
        rows = _xlsx_rows(client.get("/api/client-rotation/export").content)
        assert len(rows) == 1  # только шапка

    def test_holding_expansion(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Босс", "Босс Б.Б.")
        head = _company(db, t.id, "Голова", "7700000010", "7700000010")
        head.holding_id, head.is_holding_head = "7700000010", True
        member = _company(db, t.id, "Член", "7700000011", "7700000011")
        member.holding_id, member.is_holding_head = "7700000010", False
        _crd(db, t.id, head.id)
        _crd(db, t.id, member.id)
        db.add(Assignment(tenant_id=t.id, company_id=head.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        rows = _xlsx_rows(client.get("/api/client-rotation/export").content)
        names = {row[1] for row in rows[1:]}
        managers = {row[0] for row in rows[1:]}
        assert names == {"Голова", "Член"}  # холдинг развёрнут
        assert managers == {"Босс Б.Б."}    # оба под одним менеджером

    def test_summary_contact_priority(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "М", "М М.М.")
        c = _company(db, t.id, "Клиент", "7700000020", "7700000020")
        _crd(db, t.id, c.id, phone="auto-phone", contact_person="auto-name")
        db.add(Summary(tenant_id=t.id, company_id=c.id, contact_phone="verified-phone", contact_name="verified-name"))
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        rows = _xlsx_rows(client.get("/api/client-rotation/export").content)
        row = rows[1]
        assert row[2] == "verified-phone"  # summary важнее crd
        assert row[3] == "verified-name"

    def test_surrogate_inn_empty(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "М", "М М.М.")
        c = _company(db, t.id, "БезИНН", "|БезИНН|Иванов", inn=None)  # суррогат
        _crd(db, t.id, c.id)
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        rows = _xlsx_rows(client.get("/api/client-rotation/export").content)
        # ИНН пустой (openpyxl читает пустую ячейку как None), суррогат не утёк.
        assert rows[1][8] in (None, "")
        assert "|" not in (rows[1][8] or "")
