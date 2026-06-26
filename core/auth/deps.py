"""FastAPI-обвязка аутентификации: зависимость доступа и куки сессии.

Принцип deny by default: защищённый маршрут объявляет зависимость
`Depends(get_current_auth)`. Нет валидной сессии -> 401, дальше код не идёт.
tenant_id берётся ТОЛЬКО из серверной сессии (AuthContext), не из запроса клиента.
"""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as DBSession

from core.auth.service import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthContext,
    validate_session,
)
from core.db import get_db


def _is_secure_env() -> bool:
    """В prod/staging кука только по HTTPS; локально - можно по http."""
    return os.environ.get("APP_ENV", "local") in ("prod", "staging")


def set_session_cookie(response: Response, raw_token: str) -> None:
    """Поставить httponly-куку сессии с сырым токеном."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_is_secure_env(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Удалить куку сессии (logout)."""
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def get_current_auth(
    request: Request, db: DBSession = Depends(get_db)
) -> AuthContext:
    """Зависимость доступа. Валидная сессия -> AuthContext, иначе 401.

    Это единственная точка, где tenant_id попадает в запрос, - из сессии.
    """
    raw_token = request.cookies.get(SESSION_COOKIE)
    ctx = validate_session(db, raw_token) if raw_token else None
    if ctx is None:
        # Истёкшую сессию validate_session мог удалить - фиксируем удаление.
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
            headers={"WWW-Authenticate": "Cookie"},
        )
    db.commit()
    return ctx
