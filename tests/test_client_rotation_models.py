"""Тесты моделей модуля «Ротация клиентов» (Фаза 1.1).

In-memory SQLite (StaticPool) с PRAGMA foreign_keys=ON - чтобы честно
проверить DB-level каскады (CASCADE на companies, SET NULL на employees),
которые на проде PostgreSQL обеспечивает БД.
Проверяют: создание, JSON-поля (variant JSONB/JSON), уникальность 1:1 по
company_id, каскады при удалении компании/сотрудника.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.models import (
    Assignment,
    Base,
    ClientRotationData,
    Company,
    Employee,
    Summary,
    Tenant,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _rec):  # SQLite по умолчанию не enforce-ит FK
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

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


def _company(s, t, source_key="7707123456", inn="7707123456", name="ООО Клиент"):
    c = Company(tenant_id=t.id, source_key=source_key, inn=inn, name=name)
    s.add(c)
    s.commit()
    return c


# --- ClientRotationData ---

def test_crd_created_with_defaults(session):
    s, t = session
    c = _company(s, t)
    crd = ClientRotationData(tenant_id=t.id, company_id=c.id, score=42, current_manager="Иванов И.И.")
    s.add(crd)
    s.commit()
    found = s.get(ClientRotationData, crd.id)
    assert found.score == 42
    assert found.current_manager == "Иванов И.И."
    # Boolean-флаги имеют дефолт False (не NULL).
    assert found.is_orphan is False
    assert found.in_sp is False
    assert found.in_dsp is False


def test_crd_json_fields_roundtrip(session):
    s, t = session
    c = _company(s, t)
    crd = ClientRotationData(
        tenant_id=t.id, company_id=c.id,
        turnover_json={"2024": 1000000, "2025": 1500000},
        score_breakdown_json=[{"factor": "recency", "points": 10}],
        holding_members_json=["7707000001", "7707000002"],
    )
    s.add(crd)
    s.commit()
    s.expire(crd)
    found = s.get(ClientRotationData, crd.id)
    assert found.turnover_json == {"2024": 1000000, "2025": 1500000}
    assert found.score_breakdown_json == [{"factor": "recency", "points": 10}]
    assert found.holding_members_json == ["7707000001", "7707000002"]


def test_crd_unique_per_company(session):
    s, t = session
    c = _company(s, t)
    s.add(ClientRotationData(tenant_id=t.id, company_id=c.id))
    s.commit()
    s.add(ClientRotationData(tenant_id=t.id, company_id=c.id))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_crd_cascade_on_company_delete(session):
    s, t = session
    c = _company(s, t)
    crd = ClientRotationData(tenant_id=t.id, company_id=c.id, score=1)
    s.add(crd)
    s.commit()
    crd_id = crd.id
    s.delete(c)
    s.commit()
    s.expire_all()  # DB-level CASCADE невидим для identity map - перечитываем
    # CASCADE: удаление компании уносит её данные ротации.
    assert s.get(ClientRotationData, crd_id) is None


# --- Assignment ---

def test_assignment_created_with_employee(session):
    s, t = session
    c = _company(s, t)
    emp = Employee(tenant_id=t.id, last_name="Принимающий", first_name="Пётр", is_active=True)
    s.add(emp)
    s.commit()
    a = Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id, transfer_status="передан")
    s.add(a)
    s.commit()
    found = s.get(Assignment, a.id)
    assert found.assigned_to_employee_id == emp.id
    assert found.transfer_status == "передан"
    assert found.updated_at is not None


def test_assignment_unique_per_company(session):
    s, t = session
    c = _company(s, t)
    s.add(Assignment(tenant_id=t.id, company_id=c.id, transfer_status="свой"))
    s.commit()
    s.add(Assignment(tenant_id=t.id, company_id=c.id, transfer_status="другой"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_assignment_employee_set_null_on_delete(session):
    s, t = session
    c = _company(s, t)
    emp = Employee(tenant_id=t.id, last_name="Уходящий", first_name="Иван", is_active=True)
    s.add(emp)
    s.commit()
    a = Assignment(tenant_id=t.id, company_id=c.id, assigned_to_employee_id=emp.id)
    s.add(a)
    s.commit()
    a_id = a.id
    s.delete(emp)
    s.commit()
    s.expire_all()  # DB-level SET NULL невидим для identity map - перечитываем
    # SET NULL: назначение остаётся, ссылка на сотрудника обнуляется.
    found = s.get(Assignment, a_id)
    assert found is not None
    assert found.assigned_to_employee_id is None


# --- Summary ---

def test_summary_created(session):
    s, t = session
    c = _company(s, t)
    sm = Summary(tenant_id=t.id, company_id=c.id, summary="Крупный клиент", size_tier=3, workstations=150)
    s.add(sm)
    s.commit()
    found = s.get(Summary, sm.id)
    assert found.summary == "Крупный клиент"
    assert found.size_tier == 3
    assert found.workstations == 150


def test_summary_unique_per_company(session):
    s, t = session
    c = _company(s, t)
    s.add(Summary(tenant_id=t.id, company_id=c.id, summary="A"))
    s.commit()
    s.add(Summary(tenant_id=t.id, company_id=c.id, summary="B"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_summary_cascade_on_company_delete(session):
    s, t = session
    c = _company(s, t)
    sm = Summary(tenant_id=t.id, company_id=c.id, summary="X")
    s.add(sm)
    s.commit()
    sm_id = sm.id
    s.delete(c)
    s.commit()
    s.expire_all()  # DB-level CASCADE невидим для identity map - перечитываем
    assert s.get(Summary, sm_id) is None
