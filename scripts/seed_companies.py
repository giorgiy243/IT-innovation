"""Сид компаний из client_rotation.db (client-rotate).

Читает таблицу clients, отбирает записи с валидным ИНН (10 или 12 цифр),
делает upsert в companies по (tenant_id, source_key), где source_key=ИНН.
Идемпотентен: повторный запуск обновляет name/city/segment/holding_id/
is_holding_head, не дублирует строки. Клиенты без ИНН - Фаза 2.1 плана ротации.

Использование:
    python scripts/seed_companies.py --db /path/to/client_rotation.db
    python scripts/seed_companies.py --db /path/to/client_rotation.db --tenant 1
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows: cp1252 -> utf-8

from sqlalchemy import select, text

from core.db import session_scope
from core.models import Company, Tenant

INN_RE = re.compile(r"^\d{10}$|^\d{12}$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed companies from client_rotation.db")
    p.add_argument("--db", required=True, help="Путь к client_rotation.db")
    p.add_argument("--tenant", type=int, default=1, help="ID арендатора (по умолчанию 1)")
    return p.parse_args()


def load_clients(db_path: str) -> list[dict]:
    """Читает clients из client_rotation.db, фильтрует по валидному ИНН."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT inn, name, city, level, holding_id, is_holding_head FROM clients"
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        inn = r["inn"] or ""
        if not INN_RE.match(inn):
            continue
        result.append({
            # У валидного ИНН ключ = сам ИНН. Клиенты без ИНН (суррогаты) пока
            # не заводятся - расширение на них в Фазе 2.1 плана ротации.
            "source_key": inn,
            "inn": inn,
            "name": r["name"] or "",
            "city": r["city"] or None,
            "segment": r["level"] or None,
            "holding_id": r["holding_id"] or None,
            "is_holding_head": bool(r["is_holding_head"]),
        })
    return result


def seed(db_path: str, tenant_id: int) -> None:
    clients = load_clients(db_path)
    print(f"Загружено из client_rotation.db: {len(clients)} записей с валидным ИНН")

    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print(f"Арендатор id={tenant_id} не найден. Сначала создайте арендатора.")
            sys.exit(1)
        print(f"Арендатор: {tenant.name} (id={tenant.id})")

        existing = {
            c.source_key: c
            for c in db.execute(
                select(Company).where(Company.tenant_id == tenant_id)
            ).scalars().all()
        }

        created = updated = skipped = 0
        for data in clients:
            key = data["source_key"]
            if key in existing:
                c = existing[key]
                changed = False
                for field in ("name", "city", "segment", "holding_id", "is_holding_head"):
                    if getattr(c, field) != data[field]:
                        setattr(c, field, data[field])
                        changed = True
                if changed:
                    updated += 1
                else:
                    skipped += 1
            else:
                db.add(Company(tenant_id=tenant_id, **data))
                created += 1

        db.flush()

    print(f"Готово: создано {created}, обновлено {updated}, без изменений {skipped}")


if __name__ == "__main__":
    args = parse_args()
    seed(args.db, args.tenant)
