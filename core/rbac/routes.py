"""HTTP-маршруты RBAC: навигация по ролям.

/nav - меню пользователя: список модулей, которые он видит (объединение по ролям,
только включённые), с итоговой областью данных. Защищено deny by default -
без валидной сессии 401. tenant_id - из сессии, не из запроса.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.rbac.deps import get_access
from core.rbac.service import AccessProfile

router = APIRouter(tags=["rbac"])


class NavItem(BaseModel):
    code: str
    name: str
    scope: str


class NavResponse(BaseModel):
    roles: list[str]
    modules: list[NavItem]


@router.get("/nav", response_model=NavResponse)
def nav(access: AccessProfile = Depends(get_access)) -> NavResponse:
    """Навигация текущего пользователя: его роли и видимые модули по порядку."""
    return NavResponse(
        roles=list(access.role_codes),
        modules=[
            NavItem(code=m.code, name=m.name, scope=m.scope) for m in access.nav
        ],
    )
