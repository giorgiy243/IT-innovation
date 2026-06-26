"""End-to-end тесты аутентификации (Фаза 1.1).

Работают на временной in-memory SQLite (StaticPool), не трогают рабочую БД.
Проверяют: вход (верный/неверный), deny by default, сессию и выход,
а также безопасность хранения пароля и токена.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.app import app
from core.auth.passwords import hash_password
from core.auth.service import SESSION_COOKIE
from core.db import get_db
from core.models import Base, Session as SessionModel, Tenant, User

TEST_LOGIN = "ivanov"
TEST_PASSWORD = "correct horse battery"


@pytest.fixture()
def db_session():
    """Изолированная SQLite-БД на каждый тест, со всеми таблицами ядра."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    # Сидим арендатора и пользователя
    tenant = Tenant(name="АйТек")
    session.add(tenant)
    session.flush()
    session.add(
        User(
            tenant_id=tenant.id,
            login=TEST_LOGIN,
            password_hash=hash_password(TEST_PASSWORD),
        )
    )
    session.commit()
    try:
        yield session, TestingSession
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """TestClient с подменённой зависимостью get_db на тестовую сессию."""
    _, TestingSession = db_session

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_login_wrong_password(client):
    r = client.post("/login", json={"login": TEST_LOGIN, "password": "nope"})
    assert r.status_code == 401
    assert SESSION_COOKIE not in r.cookies


def test_login_unknown_user(client):
    r = client.post("/login", json={"login": "ghost", "password": "whatever"})
    assert r.status_code == 401


def test_login_success_sets_cookie(client):
    r = client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"login": TEST_LOGIN, "tenant_id": 1}
    assert SESSION_COOKIE in r.cookies


def test_me_denied_without_session(client):
    r = client.get("/me")
    assert r.status_code == 401


def test_me_allowed_with_session(client):
    client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json()["login"] == TEST_LOGIN


def test_logout_invalidates_session(client):
    client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    assert client.get("/me").status_code == 200
    r = client.post("/logout")
    assert r.status_code == 204
    assert client.get("/me").status_code == 401


def test_password_not_stored_plaintext(db_session):
    session, _ = db_session
    user = session.execute(select(User).where(User.login == TEST_LOGIN)).scalar_one()
    assert TEST_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")


def test_session_token_stored_as_hash(client, db_session):
    session, _ = db_session
    r = client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    raw_token = r.cookies[SESSION_COOKIE]
    # Сырого токена в БД нет - только его sha256-хеш.
    row = session.execute(select(SessionModel)).scalar_one()
    assert row.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    assert row.token_hash != raw_token


def test_inactive_user_cannot_login(client, db_session):
    session, _ = db_session
    user = session.execute(select(User).where(User.login == TEST_LOGIN)).scalar_one()
    user.is_active = False
    session.commit()
    r = client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    assert r.status_code == 401


def test_empty_credentials_rejected(client):
    # Pydantic min_length=1 -> 422, до сервиса не доходит.
    assert client.post("/login", json={"login": "", "password": ""}).status_code == 422


def test_long_password_accepted(db_session):
    # argon2 не имеет 72-байтного предела bcrypt - длинный пароль валиден.
    from core.auth.passwords import hash_password, verify_password

    long_pw = "ä" * 500
    h = hash_password(long_pw)
    assert verify_password(long_pw, h)
    assert not verify_password(long_pw + "x", h)


def test_expired_session_denied(client, db_session):
    from datetime import timedelta

    from core.models import utcnow

    session, _ = db_session
    client.post("/login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
    # Состарим сессию вручную.
    row = session.execute(select(SessionModel)).scalar_one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    session.commit()
    # Доступ закрыт, и протухшая сессия должна быть удалена.
    assert client.get("/me").status_code == 401
    assert session.execute(select(SessionModel)).scalar_one_or_none() is None
