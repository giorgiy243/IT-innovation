"""Сервис аутентификации: вход по логину/паролю и серверные сессии.

Чистый слой без FastAPI - принимает SQLAlchemy-сессию, работает с моделями.
Веб-обвязка (куки, ответы) - в core/auth/deps.py и эндпоинтах.

Безопасность:
- неверный логин и неверный пароль неотличимы по времени ответа
  (фиктивная проверка хеша при отсутствии пользователя - против перебора логинов);
- в БД хранится только sha256-хеш токена сессии, клиенту уходит сырой токен;
- deny by default: нет валидной сессии -> нет доступа (см. deps.py).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from core.auth.passwords import hash_password, needs_rehash, verify_password
from core.models import Session as SessionModel
from core.models import Tenant, User, utcnow

# --- Конфигурация сессий ---
SESSION_COOKIE = "itinv_session"
SESSION_TTL = timedelta(hours=12)
_TOKEN_BYTES = 32  # secrets.token_urlsafe(32) -> ~43 символа энтропии

# Фиктивный хеш: проверяем его, когда логина нет, чтобы время ответа не выдавало
# существование пользователя. Считается один раз при импорте.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class AuthContext:
    """Кто аутентифицирован. Источник истины для tenant_id в запросах."""

    user_id: int
    tenant_id: int
    login: str
    must_change_password: bool = False


class AuthError(Exception):
    """Вход не удался: неверные данные или неактивная учётка/арендатор."""


def _hash_token(token: str) -> str:
    """sha256-хекс сырого токена - то, что хранится в БД (PK сессии)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_aware_utc(dt: datetime) -> datetime:
    """Привести datetime к timezone-aware UTC.

    PostgreSQL (timestamptz) отдаёт aware-время, SQLite - naive. Чтобы сравнение
    с utcnow() не падало на разных бэкендах, наивное время трактуем как UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def authenticate(db: DBSession, login: str, password: str) -> User:
    """Проверить логин+пароль. Вернуть User или бросить AuthError.

    Старт - один арендатор, поэтому пользователь ищется по логину среди активных.
    Когда арендаторов станет больше, сюда добавится резолв tenant по хосту/коду
    (UniqueConstraint(tenant_id, login) это уже допускает).
    """
    login = (login or "").strip()
    user = db.execute(
        select(User).where(User.login == login, User.is_active.is_(True))
    ).scalar_one_or_none()

    if user is None:
        # Тратим то же время, что и на реальную проверку - не выдаём отсутствие логина.
        verify_password(password, _DUMMY_HASH)
        raise AuthError("Неверный логин или пароль")

    if not verify_password(password, user.password_hash):
        raise AuthError("Неверный логин или пароль")

    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise AuthError("Организация неактивна")

    # Параметры argon2 устарели -> пересчитываем хеш, раз пароль сейчас известен и верен.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    return user


def create_session(db: DBSession, user: User) -> str:
    """Создать серверную сессию для пользователя. Вернуть СЫРОЙ токен для куки."""
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    session_row = SessionModel(
        token_hash=_hash_token(raw_token),
        user_id=user.id,
        tenant_id=user.tenant_id,
        expires_at=utcnow() + SESSION_TTL,
    )
    db.add(session_row)
    db.flush()
    return raw_token


def validate_session(db: DBSession, raw_token: str) -> AuthContext | None:
    """Проверить сырой токен. Вернуть AuthContext или None, если сессия невалидна.

    Истёкшую сессию удаляем сразу - не копим мусор. Деактивация пользователя
    или арендатора немедленно лишает сессию силы.
    """
    if not raw_token:
        return None

    session_row = db.get(SessionModel, _hash_token(raw_token))
    if session_row is None:
        return None

    if _as_aware_utc(session_row.expires_at) <= utcnow():
        db.delete(session_row)
        return None

    user = db.get(User, session_row.user_id)
    if user is None or not user.is_active:
        db.delete(session_row)
        return None

    tenant = db.get(Tenant, session_row.tenant_id)
    if tenant is None or not tenant.is_active:
        db.delete(session_row)
        return None

    return AuthContext(
        user_id=user.id,
        tenant_id=session_row.tenant_id,
        login=user.login,
        must_change_password=user.must_change_password,
    )


def delete_session(db: DBSession, raw_token: str) -> None:
    """Завершить сессию (logout). Молча игнорирует уже отсутствующий токен."""
    if not raw_token:
        return
    session_row = db.get(SessionModel, _hash_token(raw_token))
    if session_row is not None:
        db.delete(session_row)


def cleanup_expired_sessions(db: DBSession) -> int:
    """Удалить все истёкшие сессии. Вернуть число удалённых (для обслуживания)."""
    result = db.execute(
        delete(SessionModel).where(SessionModel.expires_at <= utcnow())
    )
    return result.rowcount or 0
