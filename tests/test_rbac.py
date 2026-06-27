"""End-to-end и юнит-тесты RBAC (Фаза 1.3).

Работают на временной in-memory SQLite (StaticPool), не трогают рабочую БД.
Проверяют: навигацию по ролям (объединение модулей, только включённые),
deny by default через require_module (401 без сессии, 403 без модуля),
выбор самого широкого scope при нескольких ролях.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
)
from core.rbac.deps import require_module
from core.rbac.routes import router as rbac_router
from core.rbac.service import ModuleAccess, load_access, widest_scope

PW = "correct horse battery"

# --- Самодостаточное тест-приложение: реальные роутеры + пробные защищённые маршруты ---
probe_app = FastAPI()
probe_app.include_router(auth_router)
probe_app.include_router(rbac_router)


@probe_app.get("/probe/sales")
def _probe_sales(access: ModuleAccess = Depends(require_module("sales"))):
    return {"code": access.code, "scope": access.scope}


@probe_app.get("/probe/presale")
def _probe_presale(access: ModuleAccess = Depends(require_module("presale"))):
    return {"code": access.code, "scope": access.scope}


def _seed(s) -> None:
    tenant = Tenant(name="АйТек")
    s.add(tenant)
    s.flush()

    s.add_all(
        [
            Module(code="sales", name="Продажи", is_enabled=True, sort_order=10),
            Module(code="presale", name="Presale", is_enabled=True, sort_order=20),
            # Выключенный модуль: роль его перечисляет, но в навигацию он не попадает.
            Module(code="marketing", name="Маркетинг", is_enabled=False, sort_order=30),
        ]
    )

    mop = Role(tenant_id=tenant.id, code="mop", name="МОП")
    rop = Role(tenant_id=tenant.id, code="rop", name="РОП")
    analyst = Role(tenant_id=tenant.id, code="analyst", name="Аналитик")
    security = Role(tenant_id=tenant.id, code="security", name="ИБ")
    s.add_all([mop, rop, analyst, security])
    s.flush()

    s.add_all(
        [
            RoleModule(role_id=mop.id, module_code="sales"),
            RoleModule(role_id=rop.id, module_code="sales"),
            RoleModule(role_id=analyst.id, module_code="sales"),
            RoleModule(role_id=analyst.id, module_code="presale"),
            RoleModule(role_id=analyst.id, module_code="marketing"),  # выключен
        ]
    )

    pw = hash_password(PW)
    ivanov = User(tenant_id=tenant.id, login="ivanov", password_hash=pw)  # МОП own
    petrov = User(tenant_id=tenant.id, login="petrov", password_hash=pw)  # МОП+РОП
    admin = User(tenant_id=tenant.id, login="admin", password_hash=pw)    # analyst all
    sec = User(tenant_id=tenant.id, login="sec", password_hash=pw)        # security, без модулей
    newbie = User(tenant_id=tenant.id, login="newbie", password_hash=pw)  # вообще без ролей
    s.add_all([ivanov, petrov, admin, sec, newbie])
    s.flush()

    s.add_all(
        [
            UserRole(user_id=ivanov.id, role_id=mop.id, scope="own"),
            # Кейс Первухина: один пользователь - две роли на один модуль.
            UserRole(user_id=petrov.id, role_id=mop.id, scope="own"),
            UserRole(user_id=petrov.id, role_id=rop.id, scope="team", scope_ref="group-1"),
            UserRole(user_id=admin.id, role_id=analyst.id, scope="all"),
            UserRole(user_id=sec.id, role_id=security.id, scope="all"),
        ]
    )


@pytest.fixture()
def db_session():
    """Изолированная SQLite-БД на каждый тест, засеянная ролями/модулями."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    _seed(session)
    session.commit()
    try:
        yield session, TestingSession
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    _, TestingSession = db_session

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    probe_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(probe_app)
    probe_app.dependency_overrides.clear()


def login(client: TestClient, username: str) -> None:
    r = client.post("/login", json={"login": username, "password": PW})
    assert r.status_code == 200, r.text


def _uid(session, login_name: str) -> tuple[int, int]:
    user = session.execute(select(User).where(User.login == login_name)).scalar_one()
    return user.id, user.tenant_id


# --- Навигация ---

def test_nav_denied_without_session(client):
    assert client.get("/nav").status_code == 401


def test_nav_mop_sees_only_sales(client):
    login(client, "ivanov")
    r = client.get("/nav")
    assert r.status_code == 200
    body = r.json()
    assert [m["code"] for m in body["modules"]] == ["sales"]
    assert body["modules"][0]["scope"] == "own"
    assert body["roles"] == ["mop"]


def test_nav_analyst_sees_enabled_modules_in_order(client):
    login(client, "admin")
    body = client.get("/nav").json()
    # marketing выключен -> в навигации его нет; порядок по sort_order.
    assert [m["code"] for m in body["modules"]] == ["sales", "presale"]
    assert body["roles"] == ["analyst"]


def test_nav_role_without_modules_still_lists_role(client):
    # security не даёт ни одного модуля, но роль в профиле отражается.
    login(client, "sec")
    body = client.get("/nav").json()
    assert body["modules"] == []
    assert body["roles"] == ["security"]


# --- require_module (deny by default) ---

def test_require_module_denied_without_session(client):
    assert client.get("/probe/sales").status_code == 401


def test_require_module_grants_access(client):
    login(client, "ivanov")
    r = client.get("/probe/sales")
    assert r.status_code == 200
    assert r.json() == {"code": "sales", "scope": "own"}


def test_require_module_denies_unowned_module(client):
    login(client, "ivanov")  # МОП: есть sales, нет presale
    assert client.get("/probe/presale").status_code == 403


# --- Объединение прав / самый широкий scope ---

def test_widest_scope_wins_across_roles(client):
    login(client, "petrov")  # МОП own + РОП team на sales
    body = client.get("/nav").json()
    sales = next(m for m in body["modules"] if m["code"] == "sales")
    assert sales["scope"] == "team"
    assert set(body["roles"]) == {"mop", "rop"}


def test_require_module_uses_widest_scope(client):
    login(client, "petrov")
    r = client.get("/probe/sales")
    assert r.status_code == 200
    assert r.json()["scope"] == "team"


# --- Сервисный слой напрямую ---

def test_load_access_aggregates_widest_scope(db_session):
    session, _ = db_session
    uid, tid = _uid(session, "petrov")
    profile = load_access(session, uid, tid)
    assert profile.can("sales")
    assert profile.scope_for("sales") == "team"
    assert profile.scope_for("presale") is None
    assert [m.code for m in profile.nav] == ["sales"]


def test_load_access_excludes_disabled_module(db_session):
    session, _ = db_session
    uid, tid = _uid(session, "admin")
    profile = load_access(session, uid, tid)
    assert profile.can("sales")
    assert profile.can("presale")
    assert not profile.can("marketing")  # выключен в каталоге


def test_widest_scope_unit():
    assert widest_scope(["own", "team"]) == "team"
    assert widest_scope(["team", "all", "domain"]) == "all"
    assert widest_scope([]) == "own"
    # Неизвестный scope получает ранг 0 и проигрывает любому известному.
    assert widest_scope(["own", "bogus"]) == "own"


# --- Граничные кейсы ---

def test_user_without_roles_sees_nothing(client):
    # Deny by default: пользователь без ролей видит пустую навигацию (но не 401/500).
    login(client, "newbie")
    r = client.get("/nav")
    assert r.status_code == 200
    assert r.json() == {"roles": [], "modules": []}


def test_user_without_roles_denied_module(client):
    login(client, "newbie")
    assert client.get("/probe/sales").status_code == 403


def test_load_access_empty_for_user_without_roles(db_session):
    session, _ = db_session
    uid, tid = _uid(session, "newbie")
    profile = load_access(session, uid, tid)
    assert profile.role_codes == ()
    assert profile.modules == {}
    assert not profile.can("sales")


def test_load_access_ignores_cross_tenant_role(db_session):
    """Защита изоляции: пользователь tenant=1, привязанный (битой связкой) к роли
    чужого tenant=2, не получает её модули. Фильтр Role.tenant_id == tenant_id."""
    session, _ = db_session
    foreign = Tenant(name="Чужой")
    session.add(foreign)
    session.flush()
    session.add(Module(code="secret", name="Секрет", is_enabled=True, sort_order=5))
    spy = Role(tenant_id=foreign.id, code="spy", name="Шпион")
    session.add(spy)
    session.flush()
    session.add(RoleModule(role_id=spy.id, module_code="secret"))
    uid, tid = _uid(session, "ivanov")  # tid == 1 (свой арендатор)
    session.add(UserRole(user_id=uid, role_id=spy.id, scope="all"))
    session.commit()

    profile = load_access(session, uid, tid)
    # Чужой модуль и чужая роль не видны.
    assert not profile.can("secret")
    assert "spy" not in profile.role_codes
    # Свой модуль по-прежнему доступен.
    assert profile.can("sales")


def test_unknown_scope_fails_closed_to_own(db_session):
    """Повреждённый scope в БД не должен давать расширенных прав - режем до own."""
    session, _ = db_session
    uid, tid = _uid(session, "ivanov")
    user_role = session.execute(
        select(UserRole).where(UserRole.user_id == uid)
    ).scalar_one()
    user_role.scope = "garbage"
    session.commit()

    profile = load_access(session, uid, tid)
    assert profile.can("sales")  # модуль остаётся доступен
    assert profile.scope_for("sales") == "own"  # но с минимальной областью
