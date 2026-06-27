"""RBAC-сервис: какие модули и с какой областью данных доступны пользователю.

Чистый слой без FastAPI - принимает SQLAlchemy-сессию и id пользователя,
возвращает AccessProfile. Веб-обвязка (зависимости, 401/403) - в deps.py.

Правила доступа (см. AI/platform/роли_и_доступ.md):
- доступ = (модули роли) × (область данных роли);
- несколько ролей у пользователя -> объединение прав (аддитивная модель);
- по каждому модулю берётся самый широкий scope среди ролей, дающих к нему доступ
  (all > domain > team > own);
- выключенный модуль (modules.is_enabled=false) в навигацию не попадает,
  даже если роль его перечисляет;
- deny by default: нет роли с этим модулем -> модуля для пользователя нет.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import Module, Role, RoleModule, UserRole

# Ранг области данных: чем больше, тем шире доступ. Единый источник истины
# (модель хранит scope строкой; здесь - его порядок).
SCOPE_RANK: dict[str, int] = {"own": 1, "team": 2, "domain": 3, "all": 4}


def widest_scope(scopes: list[str]) -> str:
    """Самый широкий scope из набора. Пустой набор -> 'own' (минимум)."""
    if not scopes:
        return "own"
    return max(scopes, key=lambda s: SCOPE_RANK.get(s, 0))


@dataclass(frozen=True)
class ModuleAccess:
    """Доступ пользователя к одному модулю: имя для меню + итоговый scope."""

    code: str
    name: str
    scope: str  # самый широкий scope среди ролей, дающих этот модуль
    scope_ref: str | None  # уточнение границы для team/domain (если задано)
    sort_order: int


@dataclass(frozen=True)
class AccessProfile:
    """Сводный доступ пользователя. Источник навигации и проверок require_module."""

    user_id: int
    tenant_id: int
    role_codes: tuple[str, ...]
    # Только включённые модули, к которым у пользователя есть доступ.
    modules: dict[str, ModuleAccess]
    # Те же модули, упорядоченные для меню (sort_order, затем code).
    nav: tuple[ModuleAccess, ...]

    def can(self, module_code: str) -> bool:
        """Есть ли у пользователя доступ к модулю."""
        return module_code in self.modules

    def scope_for(self, module_code: str) -> str | None:
        """Итоговый scope по модулю или None, если доступа нет."""
        access = self.modules.get(module_code)
        return access.scope if access else None


def load_access(db: DBSession, user_id: int, tenant_id: int) -> AccessProfile:
    """Собрать AccessProfile пользователя по его ролям.

    Два запроса в рамках tenant: (1) user_roles -> roles -> role_modules ->
    modules (только включённые) - по каждому модулю агрегируем самый широкий
    scope; (2) коды всех ролей (включая роли без модулей, напр. security),
    чтобы профиль честно отражал роли для отображения/отладки.
    """
    # tenant_id обязателен в фильтре: даже если связка user_roles окажется битой
    # и сошлётся на роль чужого арендатора, доступ к ней не утечёт (defense in depth).
    # tenant_id приходит из серверной сессии (AuthContext), не от клиента.
    rows = db.execute(
        select(Module, UserRole.scope, UserRole.scope_ref, Role.code)
        .join(RoleModule, RoleModule.module_code == Module.code)
        .join(Role, Role.id == RoleModule.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            Role.tenant_id == tenant_id,
            Module.is_enabled.is_(True),
        )
    ).all()

    modules: dict[str, ModuleAccess] = {}
    role_codes: set[str] = set()

    for module, scope, scope_ref, role_code in rows:
        role_codes.add(role_code)
        scope_rank = SCOPE_RANK.get(scope, 0)  # неизвестный scope -> ранг 0
        current = modules.get(module.code)
        current_rank = SCOPE_RANK.get(current.scope, 0) if current is not None else -1
        if scope_rank > current_rank:
            modules[module.code] = ModuleAccess(
                code=module.code,
                name=module.name,
                # Fail-closed: повреждённый scope не даёт лишних прав, режем до own.
                scope=scope if scope in SCOPE_RANK else "own",
                scope_ref=scope_ref,
                sort_order=module.sort_order,
            )

    # Роли без единого включённого модуля (напр. security - только аудит ядра)
    # не попадут в выборку выше. Дочитываем их коды отдельно (в рамках tenant),
    # чтобы профиль честно отражал все роли пользователя.
    all_role_codes = db.execute(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id, Role.tenant_id == tenant_id)
    ).scalars().all()
    role_codes.update(all_role_codes)

    nav = tuple(
        sorted(modules.values(), key=lambda m: (m.sort_order, m.code))
    )

    return AccessProfile(
        user_id=user_id,
        tenant_id=tenant_id,
        role_codes=tuple(sorted(role_codes)),
        modules=modules,
        nav=nav,
    )
