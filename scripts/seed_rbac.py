"""CLI: засеять стартовый каталог RBAC (модули, роли, доступ роль->модуль).

Идемпотентно: повторный запуск не плодит дубли и не затирает ручные правки
админа (is_enabled у модуля при повторе сохраняется). Источник данных -
core/rbac/defaults.py. Роли создаются в рамках арендатора (по умолчанию АйТек).

Примеры:
  python scripts/seed_rbac.py
  python scripts/seed_rbac.py --tenant "АйТек"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows-консоль по умолчанию cp1252 и падает на кириллице в выводе.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.db import session_scope  # noqa: E402
from core.models import Module, Role, RoleModule, Tenant  # noqa: E402
from core.rbac.defaults import (  # noqa: E402
    DEFAULT_MODULES,
    DEFAULT_ROLES,
    ROLE_MODULE_GRANTS,
    SUPERADMIN_ROLE,
)

DEFAULT_TENANT = "АйТек"


def get_or_create_tenant(db, name: str) -> Tenant:
    tenant = db.execute(
        select(Tenant).where(Tenant.name == name)
    ).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=name)
        db.add(tenant)
        db.flush()
        print(f"Создан арендатор: {name} (id={tenant.id})")
    return tenant


def seed_modules(db) -> None:
    """Завести/обновить каталог модулей. is_enabled при повторе не трогаем."""
    for code, name, is_enabled, sort_order in DEFAULT_MODULES:
        module = db.get(Module, code)
        if module is None:
            db.add(
                Module(code=code, name=name, is_enabled=is_enabled, sort_order=sort_order)
            )
            print(f"Модуль создан: {code} ({'вкл' if is_enabled else 'выкл'})")
        else:
            # Имя и порядок подтягиваем из дефолтов; включённость - на усмотрение админа.
            module.name = name
            module.sort_order = sort_order


def seed_roles(db, tenant: Tenant) -> dict[str, Role]:
    """Завести/обновить роли арендатора. Вернуть карту code -> Role."""
    roles: dict[str, Role] = {}
    for code, name in DEFAULT_ROLES:
        role = db.execute(
            select(Role).where(Role.tenant_id == tenant.id, Role.code == code)
        ).scalar_one_or_none()
        if role is None:
            role = Role(tenant_id=tenant.id, code=code, name=name)
            db.add(role)
            db.flush()
            print(f"Роль создана: {code}")
        else:
            role.name = name
        roles[code] = role
    return roles


def grant(db, role: Role, module_code: str) -> None:
    """Идемпотентно выдать роли доступ к модулю."""
    if db.get(Module, module_code) is None:
        return  # модуля нет в каталоге - пропускаем
    exists = db.get(RoleModule, (role.id, module_code))
    if exists is None:
        db.add(RoleModule(role_id=role.id, module_code=module_code))
        print(f"Доступ: {role.code} -> {module_code}")


def seed_role_modules(db, roles: dict[str, Role]) -> None:
    """Раздать модули ролям по дефолтам; суперадмину - все включённые модули."""
    for role_code, module_codes in ROLE_MODULE_GRANTS.items():
        role = roles.get(role_code)
        if role is None:
            continue
        for module_code in module_codes:
            grant(db, role, module_code)

    # Суперадмин (analyst) видит все ВКЛЮЧЁННЫЕ модули.
    superadmin = roles.get(SUPERADMIN_ROLE)
    if superadmin is not None:
        enabled = db.execute(
            select(Module.code).where(Module.is_enabled.is_(True))
        ).scalars().all()
        for module_code in enabled:
            grant(db, superadmin, module_code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Засеять стартовый RBAC.")
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT, help=f"Имя арендатора (по умолчанию {DEFAULT_TENANT})."
    )
    args = parser.parse_args()

    with session_scope() as db:
        tenant = get_or_create_tenant(db, args.tenant)
        seed_modules(db)
        db.flush()
        roles = seed_roles(db, tenant)
        seed_role_modules(db, roles)
        print("RBAC засеян.")


if __name__ == "__main__":
    main()
