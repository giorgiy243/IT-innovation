"""Аутентификация: вход, сессии (в БД), выход.

Своя аутентификация, без обязательных внешних провайдеров (ADR-002, самодостаточность).
Пароли - только в виде хешей (argon2), никогда в открытом виде.

Публичный API пакета:
- passwords: hash_password, verify_password, needs_rehash
- service:   authenticate, create_session, validate_session, delete_session,
             cleanup_expired_sessions, AuthContext, AuthError, SESSION_COOKIE, SESSION_TTL
- deps:      get_current_auth (deny by default), set_session_cookie, clear_session_cookie
"""
from core.auth.deps import (
    clear_session_cookie,
    get_current_auth,
    set_session_cookie,
)
from core.auth.passwords import hash_password, needs_rehash, verify_password
from core.auth.service import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthContext,
    AuthError,
    authenticate,
    cleanup_expired_sessions,
    create_session,
    delete_session,
    validate_session,
)

__all__ = [
    "hash_password",
    "verify_password",
    "needs_rehash",
    "authenticate",
    "create_session",
    "validate_session",
    "delete_session",
    "cleanup_expired_sessions",
    "AuthContext",
    "AuthError",
    "SESSION_COOKIE",
    "SESSION_TTL",
    "get_current_auth",
    "set_session_cookie",
    "clear_session_cookie",
]
