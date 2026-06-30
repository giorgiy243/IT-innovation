"""Сервис модуля «Ротация клиентов» - чистая логика без FastAPI.

Видимость данных (scope, см. AI/platform/роли_и_доступ.md):
  - all / domain - все клиенты арендатора;
  - team         - клиенты пользователя + его подчинённых (employees.manager_id);
  - own          - только клиенты, где пользователь - текущий менеджер.
Связь «клиент -> менеджер» идёт по client_rotation_data.current_manager (строка
crm_name). Пользователь без привязки к employee при scope own/team не видит
ничего (fail-closed) - PII не утекает.

Эффективный статус передачи: ручной override из assignments важнее исходного
из client_rotation_data (как в client-rotate).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import (
    Assignment,
    ClientRotationData,
    Company,
    Employee,
    Summary,
    User,
)

# scope, при которых менеджерный фильтр не накладывается (видно всё в tenant).
_UNRESTRICTED_SCOPES = frozenset({"all", "domain"})


def get_user_employee(db: DBSession, tenant_id: int, user_id: int) -> Employee | None:
    """Сотрудник, привязанный к пользователю (User.employee_id), или None."""
    user = db.get(User, user_id)
    if user is None or user.employee_id is None:
        return None
    emp = db.get(Employee, user.employee_id)
    if emp is None or emp.tenant_id != tenant_id:
        return None
    return emp


def visible_manager_names(
    db: DBSession, tenant_id: int, scope: str, employee: Employee | None
) -> set[str] | None:
    """Множество crm_name, чьих клиентов видит пользователь. None = без ограничения.

    - all/domain -> None (все клиенты арендатора);
    - team       -> {сам} + подчинённые (один уровень по manager_id);
    - own        -> {сам};
    - нет employee при own/team -> пустое множество (ничего не видно).
    """
    if scope in _UNRESTRICTED_SCOPES:
        return None
    if employee is None or not employee.crm_name:
        return set()
    names = {employee.crm_name}
    if scope == "team":
        subordinates = db.execute(
            select(Employee.crm_name).where(
                Employee.tenant_id == tenant_id,
                Employee.manager_id == employee.id,
                Employee.crm_name.isnot(None),
            )
        ).scalars().all()
        names.update(subordinates)
    return names


def list_clients(
    db: DBSession,
    tenant_id: int,
    *,
    scope: str,
    employee: Employee | None,
    q: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Клиенты ротации, видимые пользователю по scope, отсортированные по score DESC.

    JOIN: companies + client_rotation_data (+ assignments, summaries, принимающий
    сотрудник - опционально). Фильтры q (имя/ИНН) и status (эффективный статус)
    применяются после сборки эффективного статуса.
    """
    names = visible_manager_names(db, tenant_id, scope, employee)
    if names is not None and not names:
        return []  # fail-closed: нет своих менеджеров - нет видимых клиентов

    stmt = (
        select(Company, ClientRotationData, Assignment, Summary, Employee.crm_name)
        .join(ClientRotationData, ClientRotationData.company_id == Company.id)
        .outerjoin(Assignment, Assignment.company_id == Company.id)
        .outerjoin(Summary, Summary.company_id == Company.id)
        .outerjoin(Employee, Employee.id == Assignment.assigned_to_employee_id)
        .where(Company.tenant_id == tenant_id)
    )
    if names is not None:
        stmt = stmt.where(ClientRotationData.current_manager.in_(names))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Company.name.ilike(like) | Company.inn.ilike(like))
    stmt = stmt.order_by(ClientRotationData.score.desc().nullslast())

    items = [_to_list_item(*row) for row in db.execute(stmt).all()]
    if status:
        items = [it for it in items if it["transfer_status"] == status]
    return items


def _to_list_item(
    company: Company,
    crd: ClientRotationData,
    assignment: Assignment | None,
    summary: Summary | None,
    assigned_to: str | None,
) -> dict:
    """Сборка строки выдачи. Эффективный статус: override из assignment важнее."""
    effective_status = crd.transfer_status
    comment = None
    if assignment is not None:
        if assignment.transfer_status:
            effective_status = assignment.transfer_status
        comment = assignment.comment
    return {
        "company_id": company.id,
        "inn": company.inn,
        "name": company.name,
        "city": company.city,
        "holding_id": company.holding_id,
        "is_holding_head": company.is_holding_head,
        "current_manager": crd.current_manager,
        "industry": crd.industry,
        "score": crd.score,
        "is_orphan": crd.is_orphan,
        "in_sp": crd.in_sp,
        "in_dsp": crd.in_dsp,
        "days_no_contact": crd.days_no_contact,
        "days_no_kp": crd.days_no_kp,
        "days_no_shipment": crd.days_no_shipment,
        "phone": crd.phone,
        "contact_person": crd.contact_person,
        "email": crd.email,
        "site": crd.site,
        "employees": crd.employees,
        "activity": crd.activity,
        "dsp_info": crd.dsp_info,
        "sp_info": crd.sp_info,
        "summary": crd.summary,
        "recommendation": crd.recommendation,
        "transfer_status": effective_status,
        "assigned_to": assigned_to,
        "assigned_to_employee_id": assignment.assigned_to_employee_id if assignment else None,
        "comment": comment,
        # Из LLM-саммари (summaries), приоритетный проверенный контакт.
        "summary_llm": summary.summary if summary else None,
        "size_tier": summary.size_tier if summary else None,
        "workstations": summary.workstations if summary else None,
        "v_contact_name": summary.contact_name if summary else None,
        "v_contact_phone": summary.contact_phone if summary else None,
    }


def list_receiving_managers(db: DBSession, tenant_id: int) -> list[dict]:
    """Активные сотрудники с crm_name - кандидаты в принимающие менеджеры.

    Источник принимающих в client-rotate - менеджеры продаж. Здесь отдаём всех
    активных с crm_name (роль уточняется при наличии role_code); уволенные
    (is_active=False) исключены.
    """
    rows = db.execute(
        select(Employee.id, Employee.crm_name)
        .where(
            Employee.tenant_id == tenant_id,
            Employee.is_active.is_(True),
            Employee.crm_name.isnot(None),
        )
        .order_by(Employee.crm_name)
    ).all()
    return [{"employee_id": eid, "crm_name": crm} for eid, crm in rows]


_UNSET = object()  # «поле не передано» - в отличие от None («очистить»).


def upsert_assignment(
    db: DBSession,
    tenant_id: int,
    *,
    company_id: int,
    assigned_to_employee_id: int | None | object = _UNSET,
    comment: str | None | object = _UNSET,
    transfer_status: str | None | object = _UNSET,
) -> Assignment:
    """Создать или обновить назначение по (tenant_id, company_id). 1:1 к компании.

    Частичное обновление: переданные поля записываются, _UNSET - не трогаются.
    Это позволяет инлайн-редактированию менять только статус ИЛИ только
    принимающего, не обнуляя остальные поля назначения.
    """
    existing = db.execute(
        select(Assignment).where(
            Assignment.tenant_id == tenant_id,
            Assignment.company_id == company_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Assignment(tenant_id=tenant_id, company_id=company_id)
        db.add(existing)
    if assigned_to_employee_id is not _UNSET:
        existing.assigned_to_employee_id = assigned_to_employee_id
    if comment is not _UNSET:
        existing.comment = comment
    if transfer_status is not _UNSET:
        existing.transfer_status = transfer_status
    return existing


def company_in_tenant(db: DBSession, tenant_id: int, company_id: int) -> bool:
    """Принадлежит ли компания арендателю (защита от записи в чужой tenant)."""
    cid = db.execute(
        select(Company.id).where(Company.id == company_id, Company.tenant_id == tenant_id)
    ).scalar_one_or_none()
    return cid is not None


def employee_in_tenant(db: DBSession, tenant_id: int, employee_id: int) -> bool:
    """Принадлежит ли сотрудник арендателю (валидация назначаемого менеджера)."""
    eid = db.execute(
        select(Employee.id).where(Employee.id == employee_id, Employee.tenant_id == tenant_id)
    ).scalar_one_or_none()
    return eid is not None
