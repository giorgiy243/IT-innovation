"""Сид менеджеров из client_rotation.db в employees (Фаза 2.2 плана ротации).

Источник имён - assignments.assigned_to (принимающие) + clients.current_manager
(текущие владельцы). Формат «Фамилия И.О.» -> last_name=Фамилия, first_name=И.О.;
crm_name = полная строка (по ней идёт резолв назначений в seed_client_rotation).
Уволенных (DEPARTED, перенесено из client-rotate managers.py) помечаем
is_active=False, не пропускаем - на них могут ссылаться исторические назначения.

Идемпотентно: повторный запуск не дублирует (ищем existing по crm_name и по ФИО).

Использование:
    PYTHONPATH=. python scripts/seed_managers.py --db /path/to/client_rotation.db --tenant 3
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows: cp1252 -> utf-8

from sqlalchemy import select

from core.db import session_scope
from core.models import Employee, Tenant

# Уволенные на момент переноса (источник: client-rotate managers.py).
DEPARTED = {"Горский М.Ю.", "Кашипов С.Р.", "Ягодная А.С."}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed managers from client_rotation.db into employees")
    p.add_argument("--db", required=True, help="Путь к client_rotation.db")
    p.add_argument("--tenant", type=int, default=1, help="ID арендатора")
    return p.parse_args()


def load_manager_names(db_path: str) -> list[str]:
    """Уникальные имена менеджеров: принимающие (assigned_to) + текущие."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    names: set[str] = set()
    for row in conn.execute(
        "SELECT DISTINCT assigned_to AS n FROM assignments "
        "WHERE assigned_to IS NOT NULL AND assigned_to <> ''"
    ):
        names.add(row["n"].strip())
    for row in conn.execute(
        "SELECT DISTINCT current_manager AS n FROM clients "
        "WHERE current_manager IS NOT NULL AND current_manager <> ''"
    ):
        names.add(row["n"].strip())
    conn.close()
    return sorted(names)


def split_fio(full: str) -> tuple[str, str | None]:
    """«Фамилия И.О.» -> (Фамилия, 'И.О.'); «Фамилия» -> (Фамилия, None)."""
    parts = full.split(None, 1)
    return parts[0], (parts[1] if len(parts) > 1 else None)


def seed(db_path: str, tenant_id: int) -> None:
    names = load_manager_names(db_path)
    print(f"Менеджеров в источнике (уникальных): {len(names)}")

    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print(f"Арендатор id={tenant_id} не найден.")
            sys.exit(1)
        print(f"Арендатор: {tenant.name} (id={tenant.id})")

        existing = db.execute(
            select(Employee).where(Employee.tenant_id == tenant_id)
        ).scalars().all()
        by_crm = {e.crm_name: e for e in existing if e.crm_name}
        by_fio = {(e.last_name, e.first_name, e.middle_name): e for e in existing}

        created = updated = skipped = 0
        for name in names:
            last, first = split_fio(name)
            active = name not in DEPARTED

            emp = by_crm.get(name) or by_fio.get((last, first, None))
            if emp is not None:
                changed = False
                if emp.crm_name != name:
                    emp.crm_name = name
                    changed = True
                if emp.is_active != active:
                    emp.is_active = active
                    changed = True
                updated += changed
                skipped += (not changed)
            else:
                db.add(Employee(
                    tenant_id=tenant_id, last_name=last, first_name=first,
                    crm_name=name, is_active=active,
                ))
                # Регистрируем, чтобы в этом же прогоне не создать дубль-тёзку.
                by_crm[name] = by_fio[(last, first, None)] = True  # type: ignore[assignment]
                created += 1

        db.flush()

    print(f"Готово: создано {created}, обновлено {updated}, без изменений {skipped}")
    print(f"Помечены уволенными (is_active=False): {sorted(DEPARTED)}")


if __name__ == "__main__":
    args = parse_args()
    seed(args.db, args.tenant)
