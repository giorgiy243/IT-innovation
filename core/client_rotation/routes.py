"""API-маршруты модуля «Ротация клиентов».

Все маршруты требуют: (1) валидной сессии - 401; (2) доступа к модулю
"client_rotation" - 403 (роль rop или analyst). tenant_id - ТОЛЬКО из сессии.
Видимость клиентов фильтруется по scope роли (own/team/domain/all).

GET  /api/client-rotation/clients      - список клиентов (по scope) + фильтры
GET  /api/client-rotation/managers     - принимающие менеджеры (активные employees)
POST /api/client-rotation/assignments  - назначить менеджера / задать статус
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.service import AuthContext
from core.client_rotation.service import (
    company_in_tenant,
    employee_in_tenant,
    get_user_employee,
    list_clients,
    list_receiving_managers,
    upsert_assignment,
)
from core.db import get_db
from core.rbac.deps import require_module
from core.rbac.service import ModuleAccess

router = APIRouter(prefix="/api/client-rotation", tags=["client_rotation"])

_MODULE = "client_rotation"


@router.get("/clients")
def api_clients(
    q: str | None = Query(None, max_length=150),
    status_filter: str | None = Query(None, max_length=100, alias="status"),
    access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Список видимых клиентов (по scope роли), отсортированных по score.

    Фильтры: q (имя/ИНН), status (эффективный статус передачи).
    """
    employee = get_user_employee(db, auth.tenant_id, auth.user_id)
    clients = list_clients(
        db, auth.tenant_id, scope=access.scope, employee=employee, q=q, status=status_filter
    )
    return {"clients": clients, "total": len(clients), "scope": access.scope}


@router.get("/managers")
def api_managers(
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[dict]:
    """Принимающие менеджеры: активные сотрудники арендатора с crm_name."""
    return list_receiving_managers(db, auth.tenant_id)


@router.post("/assignments")
def api_save_assignment(
    body: dict = Body(...),
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Назначить принимающего менеджера и/или задать статус передачи клиента.

    Тело: company_id (обязательно), assigned_to_employee_id (опц.), comment (опц.),
    transfer_status (опц.). Запись валидируется в границах арендатора.
    """
    company_id = body.get("company_id")
    if not isinstance(company_id, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="company_id обязателен (int)",
        )
    if not company_in_tenant(db, auth.tenant_id, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")

    emp_id = body.get("assigned_to_employee_id")
    if emp_id is not None:
        if not isinstance(emp_id, int) or not employee_in_tenant(db, auth.tenant_id, emp_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="assigned_to_employee_id неизвестен",
            )

    assignment = upsert_assignment(
        db, auth.tenant_id,
        company_id=company_id,
        assigned_to_employee_id=emp_id,
        comment=(body.get("comment") or None),
        transfer_status=(body.get("transfer_status") or None),
    )
    db.commit()
    return {
        "ok": True,
        "company_id": assignment.company_id,
        "assigned_to_employee_id": assignment.assigned_to_employee_id,
        "transfer_status": assignment.transfer_status,
    }
