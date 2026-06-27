"""RBAC: роли, права, область данных (scope).

Доступ = (модули роли) × (область данных роли). Deny by default. Права - в БД,
не в коде. Несколько ролей у пользователя -> объединение прав (наибольший scope).
Единая точка проверки доступа для всех модулей (см. AI/platform/роли_и_доступ.md).

Публичный API пакета:
- service: load_access, widest_scope, AccessProfile, ModuleAccess, SCOPE_RANK
- deps:    get_access (профиль доступа), require_module (deny by default -> 403)
- routes:  router (/nav)
"""
from core.rbac.deps import get_access, require_module
from core.rbac.routes import router
from core.rbac.service import (
    SCOPE_RANK,
    AccessProfile,
    ModuleAccess,
    load_access,
    widest_scope,
)

__all__ = [
    "load_access",
    "widest_scope",
    "AccessProfile",
    "ModuleAccess",
    "SCOPE_RANK",
    "get_access",
    "require_module",
    "router",
]
