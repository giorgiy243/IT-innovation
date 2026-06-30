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
import zipfile

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
    ClientHandover,
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


def test_holding_members_hidden_from_list(client, db):
    # Как в client-rotate: ЮЛ-члены холдинга скрыты из списка; видны голова и
    # самостоятельные компании.
    t = _tenant(db)
    _user(db, t, "rop", "rop", "all")
    head = _company(db, t.id, "Голова", "7700000010", "7700000010")
    head.holding_id, head.is_holding_head = "7700000010", True
    member = _company(db, t.id, "ЧленХолдинга", "7700000011", "7700000011")
    member.holding_id, member.is_holding_head = "7700000010", False
    standalone = _company(db, t.id, "Самостоятельная", "7700000012", "7700000012")
    _crd(db, t.id, head.id)
    _crd(db, t.id, member.id)
    _crd(db, t.id, standalone.id)
    db.commit()
    _login(client, "rop")
    names = {c["name"] for c in client.get("/api/client-rotation/clients").json()["clients"]}
    assert "Голова" in names
    assert "Самостоятельная" in names
    assert "ЧленХолдинга" not in names  # ЮЛ-член холдинга скрыт


def test_holding_head_exposes_members(client, db):
    # Голова отдаёт состав холдинга (для счётчика «(N)» и списка в карточке).
    t = _tenant(db)
    _user(db, t, "rop", "rop", "all")
    head = _company(db, t.id, "Голова", "7700000010", "7700000010")
    head.holding_id, head.is_holding_head = "7700000010", True
    _crd(db, t.id, head.id, holding_members_json=[{"name": "ЮЛ-2", "key": "7700000011"},
                                                  {"name": "ЮЛ-3", "key": "7700000012"}])
    db.commit()
    _login(client, "rop")
    item = client.get("/api/client-rotation/clients").json()["clients"][0]
    assert item["is_holding_head"] is True
    assert [m["name"] for m in item["holding_members"]] == ["ЮЛ-2", "ЮЛ-3"]  # всего ЮЛ = 3


def test_get_client_by_key_returns_hidden_member(client, db):
    # Член холдинга скрыт из списка, но доступен по ключу (для перехода из карточки).
    t = _tenant(db)
    _user(db, t, "rop", "rop", "all")
    head = _company(db, t.id, "Голова", "7700000010", "7700000010")
    head.holding_id, head.is_holding_head = "7700000010", True
    member = _company(db, t.id, "ЧленХолдинга", "MEMBERKEY", "7700000011")
    member.holding_id, member.is_holding_head = "7700000010", False
    _crd(db, t.id, head.id)
    _crd(db, t.id, member.id, current_manager="Иванов И.И.")
    db.commit()
    _login(client, "rop")
    r = client.get("/api/client-rotation/client?key=MEMBERKEY")
    assert r.status_code == 200
    assert r.json()["name"] == "ЧленХолдинга"
    assert client.get("/api/client-rotation/client?key=NOPE").status_code == 404


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

    def test_partial_update_status_keeps_assignee(self, client, db):
        # Инлайн-смена статуса не должна обнулять ранее назначенного принимающего.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        client.post("/api/client-rotation/assignments", json={
            "company_id": c.id, "assigned_to_employee_id": emp.id, "transfer_status": "active",
        })
        # приходит только статус
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "transfer_status": "transfer"})
        row = db.query(Assignment).filter(Assignment.company_id == c.id).one()
        assert row.transfer_status == "transfer"
        assert row.assigned_to_employee_id == emp.id  # принимающий сохранён

    def test_partial_update_assignee_keeps_status(self, client, db):
        # Инлайн-смена принимающего не должна стирать статус.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "transfer_status": "progress"})
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "assigned_to_employee_id": emp.id})
        row = db.query(Assignment).filter(Assignment.company_id == c.id).one()
        assert row.assigned_to_employee_id == emp.id
        assert row.transfer_status == "progress"  # статус сохранён

    def test_unassign_sets_null(self, client, db):
        # Явный null очищает принимающего (в отличие от отсутствия ключа).
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "assigned_to_employee_id": emp.id})
        client.post("/api/client-rotation/assignments", json={"company_id": c.id, "assigned_to_employee_id": None})
        row = db.query(Assignment).filter(Assignment.company_id == c.id).one()
        assert row.assigned_to_employee_id is None

    def test_list_item_exposes_assignee_id(self, client, db):
        # Список отдаёт assigned_to_employee_id для предвыбора в инлайн-селекте.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Клиент", "7700000001", "7700000001")
        _crd(db, t.id, c.id, current_manager="Иванов И.И.")
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        data = client.get("/api/client-rotation/clients").json()
        assert data["clients"][0]["assigned_to_employee_id"] == emp.id

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

def _zip_files(content: bytes) -> dict[str, list[tuple]]:
    """Имя файла внутри ZIP -> строки его xlsx (включая шапку)."""
    out: dict[str, list[tuple]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            wb = openpyxl.load_workbook(io.BytesIO(zf.read(name)))
            out[name] = list(wb.active.iter_rows(values_only=True))
    return out


class TestExport:
    def test_returns_zip_with_per_manager_file(self, client, db):
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
        assert "zip" in r.headers["content-type"]
        files = _zip_files(r.content)
        assert "Принимающий П.П..xlsx" in files
        rows = files["Принимающий П.П..xlsx"]
        assert rows[0] == tuple(TEMPLATE_HEADERS)
        assert any(row[1] == "Альфа" and row[0] == "Принимающий П.П." for row in rows[1:])

    def test_separate_file_per_manager(self, client, db):
        # Два разных принимающих МОП -> два файла в архиве.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        e1 = _employee(db, t.id, "Первый", "Первый П.П.")
        e2 = _employee(db, t.id, "Второй", "Второй В.В.")
        c1 = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Бета", "7700000002", "7700000002")
        _crd(db, t.id, c1.id)
        _crd(db, t.id, c2.id)
        db.add(Assignment(tenant_id=t.id, company_id=c1.id, assigned_to_employee_id=e1.id))
        db.add(Assignment(tenant_id=t.id, company_id=c2.id, assigned_to_employee_id=e2.id))
        db.commit()
        _login(client, "rop")
        files = _zip_files(client.get("/api/client-rotation/export").content)
        assert set(files) == {"Первый П.П..xlsx", "Второй В.В..xlsx"}
        assert any(row[1] == "Альфа" for row in files["Первый П.П..xlsx"][1:])
        assert any(row[1] == "Бета" for row in files["Второй В.В..xlsx"][1:])

    def test_only_assigned_exported(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "БезМенеджера", "7700000002", "7700000002")
        _crd(db, t.id, c.id)
        db.add(Assignment(tenant_id=t.id, company_id=c.id, transfer_status="свой"))  # без менеджера
        db.commit()
        _login(client, "rop")
        files = _zip_files(client.get("/api/client-rotation/export").content)
        assert files == {}  # ни одного принимающего - архив пуст

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
        files = _zip_files(client.get("/api/client-rotation/export").content)
        rows = files["Босс Б.Б..xlsx"]
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
        rows = _zip_files(client.get("/api/client-rotation/export").content)["М М.М..xlsx"]
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
        rows = _zip_files(client.get("/api/client-rotation/export").content)["М М.М..xlsx"]
        # ИНН пустой (openpyxl читает пустую ячейку как None), суррогат не утёк.
        assert rows[1][8] in (None, "")
        assert "|" not in (rows[1][8] or "")


# --- выгрузка для МОП (ZIP: отдельный HTML-файл на каждого МОП) ---

def _zip_html(content: bytes) -> dict[str, str]:
    """Имя файла внутри ZIP -> его HTML-текст."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            out[name] = zf.read(name).decode("utf-8")
    return out


class TestExportManagers:
    def test_returns_zip_with_per_manager_file(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id, current_manager="Иванов И.И.", score=80)
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/export-managers")
        assert r.status_code == 200
        assert "zip" in r.headers["content-type"]
        files = _zip_html(r.content)
        assert "Принимающий П.П..html" in files
        html = files["Принимающий П.П..html"]
        assert "Альфа" in html
        assert "Высокий приоритет" in html              # вердикт по score=80
        assert "Досье для МОП: Принимающий П.П." in html  # имя МОП в заголовке файла

    def test_separate_file_per_manager(self, client, db):
        # Два разных принимающих МОП -> два HTML-файла в архиве, без перекрёстных клиентов.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        e1 = _employee(db, t.id, "Первый", "Первый П.П.")
        e2 = _employee(db, t.id, "Второй", "Второй В.В.")
        c1 = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Бета", "7700000002", "7700000002")
        _crd(db, t.id, c1.id)
        _crd(db, t.id, c2.id)
        db.add(Assignment(tenant_id=t.id, company_id=c1.id, assigned_to_employee_id=e1.id))
        db.add(Assignment(tenant_id=t.id, company_id=c2.id, assigned_to_employee_id=e2.id))
        db.commit()
        _login(client, "rop")
        files = _zip_html(client.post("/api/client-rotation/export-managers").content)
        assert set(files) == {"Первый П.П..html", "Второй В.В..html"}
        assert "Альфа" in files["Первый П.П..html"] and "Бета" not in files["Первый П.П..html"]
        assert "Бета" in files["Второй В.В..html"] and "Альфа" not in files["Второй В.В..html"]

    def test_only_assigned_clients_included(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        assigned = _company(db, t.id, "Назначенный", "7700000001", "7700000001")
        orphan = _company(db, t.id, "БезПринимающего", "7700000002", "7700000002")
        _crd(db, t.id, assigned.id)
        _crd(db, t.id, orphan.id)
        db.add(Assignment(tenant_id=t.id, company_id=assigned.id, assigned_to_employee_id=emp.id))
        db.add(Assignment(tenant_id=t.id, company_id=orphan.id, transfer_status="свой"))  # без принимающего
        db.commit()
        _login(client, "rop")
        files = _zip_html(client.post("/api/client-rotation/export-managers").content)
        joined = "".join(files.values())
        assert "Назначенный" in joined
        assert "БезПринимающего" not in joined

    def test_empty_zip_when_no_assignments(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/export-managers")
        assert r.status_code == 200
        assert _zip_html(r.content) == {}  # ни одного принимающего - архив пуст

    def test_html_escaping(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "М", "М М.М.")
        c = _company(db, t.id, "Злой <script>", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        html = _zip_html(client.post("/api/client-rotation/export-managers").content)["М М.М..html"]
        assert "<script>" not in html  # экранировано
        assert "&lt;script&gt;" in html

    def test_rich_fields_render(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "М", "М М.М.")
        head = _company(db, t.id, "Голова", "7700000010", "7700000010")
        head.is_holding_head = True
        _crd(
            db, t.id, head.id, score=75, level="Ключевой",
            in_sp=True, sp_info="ситуация в СП",
            turnover_json=[10, 20, 30], score_breakdown_json={"size": 60, "engagement": 10, "freshness": 5},
            holding_members_json=[{"name": "ЮЛ-2"}],
        )
        db.add(Assignment(
            tenant_id=t.id, company_id=head.id, assigned_to_employee_id=emp.id,
            comment="передаём потому что важно",
        ))
        db.commit()
        _login(client, "rop")
        html = _zip_html(client.post("/api/client-rotation/export-managers").content)["М М.М..html"]
        assert "Ключевой" in html                       # чип уровня
        assert "Обороты по кварталам" in html            # график оборотов
        assert "Холдинг" in html and "ЮЛ-2" in html      # разворот холдинга
        assert "передаём потому что важно" in html       # комментарий РОПа
        assert "ситуация в СП" in html                   # блок СП

    def test_403_without_module(self, client, db):
        t = _tenant(db)
        _user(db, t, "noaccess", "guest", "all", module="other_module")
        db.commit()
        _login(client, "noaccess")
        r = client.post("/api/client-rotation/export-managers")
        assert r.status_code == 403

    def test_malformed_json_does_not_crash(self, client, db):
        # Кривые turnover/score_breakdown (строки-мусор, неверный тип) не роняют 500.
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "М", "М М.М.")
        c = _company(db, t.id, "Кривой", "7700000001", "7700000001")
        _crd(
            db, t.id, c.id, score=None,
            turnover_json=["мусор", None, "12.5"], score_breakdown_json={"size": "abc"},
        )
        db.add(Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id))
        db.commit()
        _login(client, "rop")
        r = client.post("/api/client-rotation/export-managers")
        assert r.status_code == 200
        html = _zip_html(r.content)["М М.М..html"]
        assert "Кривой" in html
        assert "Низкий приоритет" in html  # score=None -> низкий


# --- регулирование передачи: журнал + инкрементальная выгрузка ---

def _assign(db, tenant_id, company_id, employee_id):
    db.add(Assignment(tenant_id=tenant_id, company_id=company_id, assigned_to_employee_id=employee_id))
    db.commit()


class TestHandoverFlow:
    def test_export_records_handover(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, emp.id)
        _login(client, "rop")
        client.post("/api/client-rotation/export-managers")
        hv = client.get("/api/client-rotation/handovers").json()
        assert len(hv) == 1
        assert hv[0]["company_name"] == "Альфа"
        assert hv[0]["manager_name"] == "Принимающий П.П."
        assert hv[0]["actor_login"] == "rop"
        assert hv[0]["handed_over_at"]

    def test_second_export_excludes_handed(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, emp.id)
        _login(client, "rop")
        first = _zip_html(client.post("/api/client-rotation/export-managers").content)
        assert "Принимающий П.П..html" in first       # первая выгрузка - есть
        second = _zip_html(client.post("/api/client-rotation/export-managers").content)
        assert second == {}                            # вторая - пусто (уже передан)
        assert len(client.get("/api/client-rotation/handovers").json()) == 1  # без дублей

    def test_mode_all_reexports_without_new_rows(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, emp.id)
        _login(client, "rop")
        client.post("/api/client-rotation/export-managers")          # пометили
        files = _zip_html(client.post("/api/client-rotation/export-managers?mode=all").content)
        assert "Принимающий П.П..html" in files                      # перевыпуск включает переданного
        assert len(client.get("/api/client-rotation/handovers").json()) == 1  # новых записей нет

    def test_reassign_makes_pending_again(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        e1 = _employee(db, t.id, "Первый", "Первый П.П.")
        e2 = _employee(db, t.id, "Второй", "Второй В.В.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, e1.id)
        _login(client, "rop")
        client.post("/api/client-rotation/export-managers")          # передан Первому
        # переназначаем Второму
        r = client.post("/api/client-rotation/assignments",
                        json={"company_id": c.id, "assigned_to_employee_id": e2.id})
        assert r.status_code == 200
        files = _zip_html(client.post("/api/client-rotation/export-managers").content)
        assert "Второй В.В..html" in files                            # снова pending для нового МОП
        hv = client.get("/api/client-rotation/handovers").json()
        assert len(hv) == 2
        assert {h["manager_name"] for h in hv} == {"Первый П.П.", "Второй В.В."}


class TestHandoverStatusInList:
    def test_clients_expose_handover_status(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, emp.id)
        _login(client, "rop")
        before = client.get("/api/client-rotation/clients").json()["clients"][0]
        assert before["handed_over_at"] is None          # назначен, не передан
        client.post("/api/client-rotation/export-managers")
        after = client.get("/api/client-rotation/clients").json()["clients"][0]
        assert after["handed_over_at"]                   # передан
        assert after["handed_over_to"] == "Принимающий П.П."


class TestHandoversApi:
    def test_filter_by_manager(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        e1 = _employee(db, t.id, "Первый", "Первый П.П.")
        e2 = _employee(db, t.id, "Второй", "Второй В.В.")
        c1 = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        c2 = _company(db, t.id, "Бета", "7700000002", "7700000002")
        _crd(db, t.id, c1.id); _crd(db, t.id, c2.id)
        _assign(db, t.id, c1.id, e1.id)
        _assign(db, t.id, c2.id, e2.id)
        _login(client, "rop")
        client.post("/api/client-rotation/export-managers")
        all_hv = client.get("/api/client-rotation/handovers").json()
        assert len(all_hv) == 2
        only_first = client.get("/api/client-rotation/handovers?manager=Первый П.П.").json()
        assert len(only_first) == 1 and only_first[0]["company_name"] == "Альфа"

    def test_filter_by_date_window(self, client, db):
        t = _tenant(db)
        _user(db, t, "rop", "rop", "all")
        emp = _employee(db, t.id, "Принимающий", "Принимающий П.П.")
        c = _company(db, t.id, "Альфа", "7700000001", "7700000001")
        _crd(db, t.id, c.id)
        _assign(db, t.id, c.id, emp.id)
        _login(client, "rop")
        client.post("/api/client-rotation/export-managers")
        assert len(client.get("/api/client-rotation/handovers?from=2000-01-01").json()) == 1
        assert len(client.get("/api/client-rotation/handovers?from=2099-01-01").json()) == 0
        assert len(client.get("/api/client-rotation/handovers?to=2000-01-01").json()) == 0

    def test_401_without_session(self, client, db):
        r = client.get("/api/client-rotation/handovers")
        assert r.status_code == 401

    def test_403_without_module(self, client, db):
        t = _tenant(db)
        _user(db, t, "noaccess", "guest", "all", module="other_module")
        db.commit()
        _login(client, "noaccess")
        r = client.get("/api/client-rotation/handovers")
        assert r.status_code == 403

    def test_tenant_isolation(self, client, db):
        # Передача в чужом арендаторе не видна.
        t1 = _tenant(db, "Один")
        t2 = _tenant(db, "Два")
        _user(db, t1, "rop1", "rop", "all")
        emp2 = _employee(db, t2.id, "Чужой", "Чужой Ч.Ч.")
        c2 = _company(db, t2.id, "ЧужойКлиент", "7700000099", "7700000099")
        _crd(db, t2.id, c2.id)
        _assign(db, t2.id, c2.id, emp2.id)
        db.add(ClientHandover(tenant_id=t2.id, company_id=c2.id, employee_id=emp2.id,
                              manager_name="Чужой Ч.Ч.", actor_login="other"))
        db.commit()
        _login(client, "rop1")
        assert client.get("/api/client-rotation/handovers").json() == []
