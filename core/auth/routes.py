"""HTTP-маршруты аутентификации: /login, /logout, /me.

Бэкенд Фазы 1.1 - JSON-эндпоинты (HTML-страница входа «Сплит» подключится
позже, когда дойдём до шаблонов; форма будет постить сюда же).
Пароль не логируется и не возвращается. Ответы об ошибке входа одинаковы
для «нет логина» и «неверный пароль» - не подсказываем перебору.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import (
    clear_session_cookie,
    get_current_auth,
    set_session_cookie,
)
from core.auth.service import (
    SESSION_COOKIE,
    AuthContext,
    AuthError,
    authenticate,
    create_session,
    delete_session,
)
from core.db import get_db

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    login: str
    tenant_id: int


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: DBSession = Depends(get_db),
) -> LoginResponse:
    """Вход по логину/паролю. Ставит httponly-куку сессии."""
    try:
        user = authenticate(db, payload.login, payload.password)
    except AuthError:
        # Единый ответ - не выдаём, логин или пароль неверен.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    raw_token = create_session(db, user)
    db.commit()  # фиксируем сессию и возможный пересчёт хеша пароля
    set_session_cookie(response, raw_token)
    return LoginResponse(login=user.login, tenant_id=user.tenant_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> Response:
    """Выход: удаляет серверную сессию и куку. Идемпотентен.

    Не требует валидной сессии: повторный или просроченный logout просто
    очищает куку и тихо завершается.
    """
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token:
        delete_session(db, raw_token)
        db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=LoginResponse)
def me(auth: AuthContext = Depends(get_current_auth)) -> LoginResponse:
    """Кто я. Защищено deny by default - без валидной сессии вернёт 401."""
    return LoginResponse(login=auth.login, tenant_id=auth.tenant_id)
