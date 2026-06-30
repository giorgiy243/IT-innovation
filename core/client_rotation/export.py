"""Выгрузка назначений ротации в формат загрузки в 1С (xlsx по шаблону ДСП).

Порт client-rotate/export_1c.py на ORM платформы. Выгружаются клиенты с
выбранным принимающим менеджером (assignments.assigned_to_employee_id задан).

РАЗВОРОТ ХОЛДИНГА: если назначена головная компания холдинга
(is_holding_head), в выгрузку попадают ВСЕ ЮЛ холдинга из базы
(company.holding_id == holding_id головы) под того же менеджера - одно
назначение переносит весь холдинг. Без дублей по company.id.

Контакты: проверенный из summaries важнее авто-контакта из client_rotation_data
(COALESCE). Суррогатный ИНН (его нет - inn=NULL) выгружается пустым.
"""
from __future__ import annotations

import io

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import (
    Assignment,
    ClientRotationData,
    Company,
    Employee,
    Summary,
)

TEMPLATE_SHEET = "Таблица"
TEMPLATE_HEADERS = [
    "Фамилия И.О.", "Наименование клиента", "Телефон", "Контактное лицо",
    "Электронная почта", "Основной вид деятельности", "Сайт",
    "Количество сотрудников", "ИНН",
]


def _client_row(db: DBSession, tenant_id: int, company_id: int) -> dict | None:
    """Поля одного клиента для выгрузки (контакты с приоритетом summaries)."""
    row = db.execute(
        select(Company, ClientRotationData, Summary)
        .join(ClientRotationData, ClientRotationData.company_id == Company.id)
        .outerjoin(Summary, Summary.company_id == Company.id)
        .where(Company.id == company_id, Company.tenant_id == tenant_id)
    ).first()
    if row is None:
        return None
    company, crd, summary = row
    s_phone = summary.contact_phone if summary else None
    s_name = summary.contact_name if summary else None
    return {
        "name": company.name,
        "inn": company.inn or "",  # суррогат (inn=NULL) -> пусто
        "phone": s_phone or crd.phone or "",
        "contact_person": s_name or crd.contact_person or "",
        "email": crd.email or "",
        "activity": crd.activity or crd.industry or "",
        "site": crd.site or "",
        "employees": crd.employees if crd.employees is not None else "",
        "is_holding_head": company.is_holding_head,
        "holding_id": company.holding_id,
    }


def _expanded_rows(db: DBSession, tenant_id: int) -> list[tuple[str, dict]]:
    """[(crm_name менеджера, client_row)] с разворотом холдингов; без дублей."""
    assigns = db.execute(
        select(Assignment.company_id, Employee.crm_name)
        .join(Employee, Employee.id == Assignment.assigned_to_employee_id)
        .where(Assignment.tenant_id == tenant_id, Employee.crm_name.isnot(None))
    ).all()

    out: list[tuple[str, dict]] = []
    seen: set[int] = set()
    for company_id, mgr in assigns:
        head = _client_row(db, tenant_id, company_id)
        if head is None:
            continue
        if company_id not in seen:
            out.append((mgr, head))
            seen.add(company_id)
        if head["is_holding_head"] and head["holding_id"]:
            member_ids = db.execute(
                select(Company.id)
                .where(
                    Company.tenant_id == tenant_id,
                    Company.holding_id == head["holding_id"],
                    Company.is_holding_head.is_(False),
                )
                .order_by(Company.name)
            ).scalars().all()
            for mid in member_ids:
                if mid in seen:
                    continue
                member = _client_row(db, tenant_id, mid)
                if member is not None:
                    out.append((mgr, member))
                    seen.add(mid)

    out.sort(key=lambda x: ((x[0] or ""), (x[1]["name"] or "")))
    return out


def export_assignments_xlsx(db: DBSession, tenant_id: int) -> bytes:
    """Возвращает .xlsx (bytes) с назначенными клиентами (с разворотом холдингов)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = TEMPLATE_SHEET
    ws.append(TEMPLATE_HEADERS)
    for mgr, r in _expanded_rows(db, tenant_id):
        ws.append([
            mgr, r["name"], r["phone"], r["contact_person"], r["email"],
            r["activity"], r["site"], r["employees"], r["inn"],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
