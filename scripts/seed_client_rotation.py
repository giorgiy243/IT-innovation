"""Сид данных модуля «Ротация клиентов» из client_rotation.db (Фаза 2.3).

Переносит три таблицы (companies и employees должны быть уже засеяны -
seed_companies.py, seed_managers.py):
  - clients      -> client_rotation_data (расширенные поля; name/city/holding_*
                    уже в companies, сюда не дублируются);
  - summaries    -> summaries (LLM-саммари + проверенный контакт);
  - assignments  -> assignments (резолв assigned_to crm_name -> employees.id).

Резолв ключей:
  - clients.inn (ИНН либо суррогат) -> companies.id по source_key;
  - assigned_to (crm_name) -> employees.id; нерезолвленные логируются,
    назначение остаётся с assigned_to_employee_id=NULL (не теряем статус).

Идемпотентно: upsert по company_id (1:1) в каждой таблице.

Использование:
    PYTHONPATH=. python scripts/seed_client_rotation.py --db /path/to/client_rotation.db --tenant 3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows: cp1252 -> utf-8

from sqlalchemy import select

from core.db import session_scope
from core.models import (
    Assignment,
    ClientRotationData,
    Company,
    Employee,
    Summary,
    Tenant,
)

# Поля clients -> client_rotation_data (то, чего нет в companies).
BOOL_FIELDS = ("is_orphan", "in_sp", "in_dsp")
JSON_FIELDS = ("turnover_json", "score_breakdown_json", "holding_members_json")
CRD_FIELDS = (
    "current_manager", "industry", "source_file",
    *BOOL_FIELDS,
    "days_no_contact", "days_no_kp", "days_no_shipment",
    "phone", "contact_person", "email", "site", "employees", "activity",
    "turnover_json", "notes", "dsp_info", "sp_info", "comments_corpus",
    "score", "score_breakdown_json", "summary", "recommendation",
    "transfer_status", "holding_members_json",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed client rotation data from client_rotation.db")
    p.add_argument("--db", required=True, help="Путь к client_rotation.db")
    p.add_argument("--tenant", type=int, default=1, help="ID арендатора")
    return p.parse_args()


def _json_or_none(val):
    if val in (None, "", "null"):
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def _read(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    clients = [dict(r) for r in conn.execute("SELECT * FROM clients")]
    summaries = [dict(r) for r in conn.execute("SELECT * FROM summaries")]
    assignments = [dict(r) for r in conn.execute("SELECT * FROM assignments")]
    conn.close()
    return clients, summaries, assignments


def _crd_values(row: dict) -> dict:
    out = {}
    for f in CRD_FIELDS:
        v = row.get(f)
        if f in BOOL_FIELDS:
            out[f] = bool(v)
        elif f in JSON_FIELDS:
            out[f] = _json_or_none(v)
        else:
            out[f] = v
    return out


def seed(db_path: str, tenant_id: int) -> None:
    clients, summaries, assignments = _read(db_path)
    print(f"Источник: clients={len(clients)}, summaries={len(summaries)}, assignments={len(assignments)}")

    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print(f"Арендатор id={tenant_id} не найден.")
            sys.exit(1)
        print(f"Арендатор: {tenant.name} (id={tenant.id})")

        # Резолв-карты.
        company_by_key = {
            c.source_key: c.id
            for c in db.execute(
                select(Company.id, Company.source_key).where(Company.tenant_id == tenant_id)
            )
        }
        emp_by_crm = {
            e.crm_name: e.id
            for e in db.execute(
                select(Employee.id, Employee.crm_name).where(
                    Employee.tenant_id == tenant_id, Employee.crm_name.isnot(None)
                )
            )
        }

        crd_existing = {r.company_id for r in db.execute(
            select(ClientRotationData.company_id).where(ClientRotationData.tenant_id == tenant_id)
        )}
        sm_existing = {r.company_id for r in db.execute(
            select(Summary.company_id).where(Summary.tenant_id == tenant_id)
        )}
        as_existing = {r.company_id for r in db.execute(
            select(Assignment.company_id).where(Assignment.tenant_id == tenant_id)
        )}

        unresolved_company = 0
        unresolved_mgr = set()

        # 1) client_rotation_data
        crd_created = 0
        for row in clients:
            cid = company_by_key.get(row.get("inn") or "")
            if cid is None:
                unresolved_company += 1
                continue
            if cid in crd_existing:
                continue
            db.add(ClientRotationData(tenant_id=tenant_id, company_id=cid, **_crd_values(row)))
            crd_existing.add(cid)
            crd_created += 1

        # 2) summaries
        sm_created = 0
        for row in summaries:
            cid = company_by_key.get(row.get("inn") or "")
            if cid is None or cid in sm_existing:
                continue
            db.add(Summary(
                tenant_id=tenant_id, company_id=cid,
                summary=row.get("summary"), size_tier=row.get("size_tier"),
                workstations=row.get("workstations"),
                contact_name=row.get("contact_name"), contact_phone=row.get("contact_phone"),
            ))
            sm_existing.add(cid)
            sm_created += 1

        # 3) assignments
        as_created = 0
        for row in assignments:
            cid = company_by_key.get(row.get("inn") or "")
            if cid is None or cid in as_existing:
                continue
            crm = (row.get("assigned_to") or "").strip()
            emp_id = emp_by_crm.get(crm) if crm else None
            if crm and emp_id is None:
                unresolved_mgr.add(crm)
            db.add(Assignment(
                tenant_id=tenant_id, company_id=cid,
                assigned_to_employee_id=emp_id,
                comment=row.get("comment"), transfer_status=row.get("transfer_status"),
            ))
            as_existing.add(cid)
            as_created += 1

        db.flush()

    print(f"Создано: client_rotation_data={crd_created}, summaries={sm_created}, assignments={as_created}")
    if unresolved_company:
        print(f"ВНИМАНИЕ: клиентов без компании в companies (пропущено): {unresolved_company}")
    if unresolved_mgr:
        print(f"ВНИМАНИЕ: нерезолвленные менеджеры ({len(unresolved_mgr)}), назначение без employee: {sorted(unresolved_mgr)}")


if __name__ == "__main__":
    args = parse_args()
    seed(args.db, args.tenant)
