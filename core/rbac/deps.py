"""FastAPI-обвязка RBAC: профиль доступа и проверка доступа к модулю.

Слои deny by default (на каждый запрос):
1. get_current_auth (core.auth.deps) - нет валидной сессии -> 401.
2. get_access - собирает AccessProfile пользователя из его ролей.
3. require_module(code) - нет роли с этим модулем -> 403.

tenant_id берётся ТОЛЬКО из серверной сессии (AuthContext), не из запроса.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.service import AuthContext
from core.db import get_db
from core.rbac.service import AccessProfile, ModuleAccess, load_access


def get_access(
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> AccessProfile:
    """Профиль доступа текущего пользователя. Требует валидной сессии (401).

    Пока пользователь не сменил временный пароль (must_change_password) - доступ
    к любым модулям закрыт (409). Сама смена пароля идёт через get_current_auth,
    не через get_access, поэтому остаётся доступной.
    """
    if auth.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Требуется смена пароля",
        )
    return load_access(db, auth.user_id, auth.tenant_id)


def require_module(module_code: str):
    """Фабрика зависимости: доступ к конкретному модулю или 403.

    Применение на маршруте модуля:
        @router.get("/sales/...")
        def view(access: ModuleAccess = Depends(require_module("sales"))):
            # access.scope - own/team/domain/all для фильтрации данных
    """

    def dependency(
        profile: AccessProfile = Depends(get_access),
    ) -> ModuleAccess:
        module_access = profile.modules.get(module_code)
        if module_access is None:
            # Не выдаём, существует ли модуль вообще - единый ответ «нет доступа».
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к модулю",
            )
        return module_access

    return dependency
