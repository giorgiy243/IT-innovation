"""Импорт вендоров из vendors_master.csv в платформу.

Читает CSV (UTF-8 с BOM, разделитель ;), делает upsert по (tenant_id, name).
Пароль портала шифруется Fernet перед записью. Дистрибьюторы пересоздаются
при каждом запуске (delete + insert) — это idempotent и безопасно.

Использование:
    python scripts/seed_vendors.py --csv /path/to/vendors_master.csv
    python scripts/seed_vendors.py --csv /path/to/vendors_master.csv --tenant 1
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from core.db import session_scope
from core.models import Tenant, Vendor, VendorDistributor
from core.vendors.crypto import encrypt

# Колонки дистрибьюторов (1-5)
_DIST_FIELDS = ["Контакт", "Email", "Телефон", "Примечание"]


def _val(row: dict, key: str) -> str | None:
    """Вернуть значение из строки CSV или None если пусто/прочерк."""
    v = row.get(key, "").strip()
    return v if v and v != "—" else None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_distributors(row: dict) -> list[dict]:
    result = []
    for n in range(1, 6):
        name = _val(row, f"Дистрибьютор {n}")
        if not name:
            continue
        result.append({
            "sort_order": n,
            "name": name,
            "contact": _val(row, f"Дист.{n} Контакт"),
            "email": _val(row, f"Дист.{n} Email"),
            "phone": _val(row, f"Дист.{n} Телефон"),
            "note": _val(row, f"Дист.{n} Примечание"),
        })
    return result


def _vendor_fields(row: dict) -> dict:
    raw_pwd = _val(row, "Портал Пароль")
    return {
        "categories": _val(row, "Категории"),
        "status_type": _val(row, "Тип статуса"),
        "status_text": _val(row, "Статус"),
        "valid_until": _parse_date(_val(row, "Действует до")),
        "partner_id": _val(row, "ID партнёра"),
        "legal_entity": _val(row, "Юридическое лицо"),
        "directions": _val(row, "Направления"),
        "discount": _val(row, "Скидка"),
        "purchase_method": _val(row, "Способ закупки"),
        "portal_url": _val(row, "Портал Ссылка"),
        "portal_login": _val(row, "Портал Логин"),
        "portal_password_enc": encrypt(raw_pwd) if raw_pwd else None,
        "vendor_contact": _val(row, "Контакт вендора"),
        "deal_registration": _val(row, "Регистрация сделки"),
        "mop_comments": _val(row, "Комментарии МОП"),
    }


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def seed(csv_path: str, tenant_id: int) -> None:
    rows = load_csv(csv_path)
    print(f"Прочитано строк из CSV: {len(rows)}")

    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print(f"Арендатор id={tenant_id} не найден.")
            sys.exit(1)
        print(f"Арендатор: {tenant.name} (id={tenant.id})")

        existing: dict[str, Vendor] = {
            v.name: v
            for v in db.execute(
                select(Vendor).where(Vendor.tenant_id == tenant_id)
            ).scalars().all()
        }

        created = updated = skipped = 0

        for row in rows:
            name = _val(row, "Название")
            if not name:
                continue

            fields = _vendor_fields(row)
            distributors = _parse_distributors(row)

            if name in existing:
                vendor = existing[name]
                changed = any(getattr(vendor, k) != v for k, v in fields.items())
                for k, v in fields.items():
                    setattr(vendor, k, v)
                # Дистрибьюторы: пересоздаём (delete + insert)
                for d in list(vendor.distributors):
                    db.delete(d)
                db.flush()
                for d in distributors:
                    db.add(VendorDistributor(vendor_id=vendor.id, **d))
                if changed or distributors:
                    updated += 1
                else:
                    skipped += 1
            else:
                vendor = Vendor(tenant_id=tenant_id, name=name, **fields)
                db.add(vendor)
                db.flush()
                for d in distributors:
                    db.add(VendorDistributor(vendor_id=vendor.id, **d))
                existing[name] = vendor
                created += 1

        db.flush()

    total_dist = sum(len(_parse_distributors(r)) for r in rows if _val(r, "Название"))
    print(f"Готово: создано {created}, обновлено {updated}, без изменений {skipped}")
    print(f"Дистрибьюторов записано: {total_dist}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Импорт вендоров из CSV")
    p.add_argument("--csv", required=True, help="Путь к vendors_master.csv")
    p.add_argument("--tenant", type=int, default=1, help="ID арендатора (по умолчанию 1)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    seed(args.csv, args.tenant)
