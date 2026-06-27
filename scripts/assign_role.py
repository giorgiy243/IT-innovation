"""CLI: назначить пользователю роль с областью данных (scope).

Роли и модули должны быть уже засеяны (scripts/seed_rbac.py). Повторное
назначение той же роли обновляет scope/scope_ref (идемпотентно).

Примеры:
  python scripts/assign_role.py --login ivanov --role mop
  python scripts/assign_role.py --login petrov --role rop --scope team --scope-ref "group-1"
  python scripts/assign_role.py --login admin --role analyst --scope all
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
from core.models import SCOPE_VALUES, Role, Tenant, User, UserRole  # noqa: E402

DEFAULT_TENANT = "АйТек"


def main() -> None:
    parser = argparse.ArgumentParser(description="Назначить пользователю роль.")
    parser.add_argument("--login", required=True, help="Логин пользователя.")
    parser.add_argument("--role", required=True, help="Код роли (mop, rop, analyst, ...).")
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT, help=f"Имя арендатора (по умолчанию {DEFAULT_TENANT})."
    )
    parser.add_argument(
        "--scope",
        default="own",
        choices=SCOPE_VALUES,
        help="Область данных: own/team/domain/all (по умолчанию own).",
    )
    parser.add_argument(
        "--scope-ref",
        default=None,
        help="Уточнение границы для team/domain (id команды/домена).",
    )
    args = parser.parse_args()

    login = args.login.strip()
    role_code = args.role.strip()

    with session_scope() as db:
        tenant = db.execute(
            select(Tenant).where(Tenant.name == args.tenant)
        ).scalar_one_or_none()
        if tenant is None:
            sys.exit(f"Ошибка: арендатор '{args.tenant}' не найден.")

        user = db.execute(
            select(User).where(User.tenant_id == tenant.id, User.login == login)
        ).scalar_one_or_none()
        if user is None:
            sys.exit(
                f"Ошибка: пользователь '{login}' не найден в '{args.tenant}'. "
                f"Создай его scripts/create_user.py."
            )

        role = db.execute(
            select(Role).where(Role.tenant_id == tenant.id, Role.code == role_code)
        ).scalar_one_or_none()
        if role is None:
            sys.exit(
                f"Ошибка: роль '{role_code}' не найдена в '{args.tenant}'. "
                f"Засей роли scripts/seed_rbac.py."
            )

        existing = db.execute(
            select(UserRole).where(
                UserRole.user_id == user.id, UserRole.role_id == role.id
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.scope = args.scope
            existing.scope_ref = args.scope_ref
            print(
                f"Обновлена роль '{role_code}' у '{login}': scope={args.scope}"
                + (f", ref={args.scope_ref}" if args.scope_ref else "")
            )
        else:
            db.add(
                UserRole(
                    user_id=user.id,
                    role_id=role.id,
                    scope=args.scope,
                    scope_ref=args.scope_ref,
                )
            )
            print(
                f"Назначена роль '{role_code}' пользователю '{login}': scope={args.scope}"
                + (f", ref={args.scope_ref}" if args.scope_ref else "")
            )


if __name__ == "__main__":
    main()
