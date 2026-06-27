"""CLI для добавления сотрудника.

Использование:
    python scripts/create_employee.py --name "Иванов А.А."
    python scripts/create_employee.py --name "Петров П.П." --email petrov@company.ru
    python scripts/create_employee.py --name "Сидоров С.С." --email s@co.ru --crm-name "Сидор"
"""
from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from core.db import session_scope
from core.models import Employee, Tenant


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Добавить сотрудника")
    p.add_argument("--name", required=True, help="ФИО (например: Иванов А.А.)")
    p.add_argument("--email", default=None, help="Email (необязательно)")
    p.add_argument("--crm-name", default=None, dest="crm_name", help="Имя в CRM (необязательно)")
    p.add_argument("--tenant", type=int, default=1, help="ID арендатора (по умолчанию 1)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    with session_scope() as db:
        tenant = db.get(Tenant, args.tenant)
        if tenant is None:
            print(f"Арендатор id={args.tenant} не найден.")
            sys.exit(1)

        existing = db.execute(
            select(Employee).where(
                Employee.tenant_id == args.tenant,
                Employee.full_name == args.name,
            )
        ).scalar_one_or_none()

        if existing is not None:
            print(f"Сотрудник «{args.name}» уже существует (id={existing.id}).")
            sys.exit(0)

        emp = Employee(
            tenant_id=args.tenant,
            full_name=args.name,
            email=args.email,
            crm_name=args.crm_name,
            is_active=True,
        )
        db.add(emp)
        db.flush()
        print(f"Создан: id={emp.id}, имя={emp.full_name}", end="")
        if emp.email:
            print(f", email={emp.email}", end="")
        print()


if __name__ == "__main__":
    main()
