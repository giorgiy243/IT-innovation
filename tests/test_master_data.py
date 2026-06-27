"""Тесты мастер-данных (Фаза 1.4): employees, companies, users.employee_id.

Работают на in-memory SQLite (StaticPool), не трогают рабочую БД.
Проверяют: создание таблиц, ограничения уникальности, FK-каскады,
связь users.employee_id -> employees.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import Base, Company, Employee, Tenant, User
from core.auth.passwords import hash_password

PW = "test-password"


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


# --- Employee ---

def test_employee_created(session):
    s, t = session
    emp = Employee(tenant_id=t.id, full_name="Иванов А.А.", email="ivanov@test.ru", is_active=True)
    s.add(emp)
    s.commit()
    assert emp.id is not None
    assert s.get(Employee, emp.id).full_name == "Иванов А.А."


def test_employee_crm_name_nullable(session):
    s, t = session
    emp = Employee(tenant_id=t.id, full_name="Петров П.П.", is_active=True)
    s.add(emp)
    s.commit()
    assert emp.crm_name is None
    assert emp.email is None


def test_employee_unique_name_per_tenant(session):
    s, t = session
    s.add(Employee(tenant_id=t.id, full_name="Сидоров С.С.", is_active=True))
    s.commit()
    s.add(Employee(tenant_id=t.id, full_name="Сидоров С.С.", is_active=True))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_employee_same_name_different_tenants_allowed(session):
    s, t = session
    t2 = Tenant(name="Другая компания")
    s.add(t2)
    s.commit()
    s.add(Employee(tenant_id=t.id, full_name="Общее имя", is_active=True))
    s.add(Employee(tenant_id=t2.id, full_name="Общее имя", is_active=True))
    s.commit()  # не должно упасть


def test_employee_deactivate_not_delete(session):
    s, t = session
    emp = Employee(tenant_id=t.id, full_name="Уволенный У.У.", is_active=True)
    s.add(emp)
    s.commit()
    emp.is_active = False
    s.commit()
    found = s.get(Employee, emp.id)
    assert found is not None
    assert found.is_active is False


# --- Company ---

def test_company_created(session):
    s, t = session
    c = Company(tenant_id=t.id, inn="7707123456", name="ООО Тест", city="Москва", segment="A")
    s.add(c)
    s.commit()
    assert c.id is not None
    assert s.get(Company, c.id).inn == "7707123456"


def test_company_unique_inn_per_tenant(session):
    s, t = session
    s.add(Company(tenant_id=t.id, inn="7707000001", name="Компания 1"))
    s.commit()
    s.add(Company(tenant_id=t.id, inn="7707000001", name="Компания 2"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_company_same_inn_different_tenants_allowed(session):
    s, t = session
    t2 = Tenant(name="Другой арендатор")
    s.add(t2)
    s.commit()
    s.add(Company(tenant_id=t.id, inn="7707000002", name="А"))
    s.add(Company(tenant_id=t2.id, inn="7707000002", name="Б"))
    s.commit()


def test_company_optional_fields_nullable(session):
    s, t = session
    c = Company(tenant_id=t.id, inn="7707000003", name="Минимум")
    s.add(c)
    s.commit()
    found = s.get(Company, c.id)
    assert found.city is None
    assert found.segment is None
    assert found.holding_id is None
    assert found.is_holding_head is False


def test_company_holding_head(session):
    s, t = session
    head = Company(tenant_id=t.id, inn="7707000010", name="Холдинг", holding_id="H1", is_holding_head=True)
    child = Company(tenant_id=t.id, inn="7707000011", name="Дочка", holding_id="H1", is_holding_head=False)
    s.add_all([head, child])
    s.commit()
    members = s.execute(
        select(Company).where(Company.holding_id == "H1", Company.tenant_id == t.id)
    ).scalars().all()
    assert len(members) == 2
    assert sum(1 for m in members if m.is_holding_head) == 1


# --- users.employee_id ---

def test_user_linked_to_employee(session):
    s, t = session
    emp = Employee(tenant_id=t.id, full_name="Менеджер М.М.", is_active=True)
    s.add(emp)
    s.commit()
    user = User(tenant_id=t.id, login="manager", password_hash=hash_password(PW), employee_id=emp.id)
    s.add(user)
    s.commit()
    assert user.employee_id == emp.id
    assert user.employee.full_name == "Менеджер М.М."


def test_user_employee_id_nullable(session):
    s, t = session
    user = User(tenant_id=t.id, login="robot", password_hash=hash_password(PW))
    s.add(user)
    s.commit()
    assert user.employee_id is None
    assert user.employee is None


def test_employee_delete_nullifies_user_link(session):
    """ondelete=SET NULL: удаление сотрудника не удаляет пользователя."""
    s, t = session
    emp = Employee(tenant_id=t.id, full_name="Временный В.В.", is_active=True)
    s.add(emp)
    s.commit()
    user = User(tenant_id=t.id, login="temp", password_hash=hash_password(PW), employee_id=emp.id)
    s.add(user)
    s.commit()

    s.delete(emp)
    s.commit()

    s.expire(user)
    assert user.employee_id is None
    assert s.get(User, user.id) is not None
