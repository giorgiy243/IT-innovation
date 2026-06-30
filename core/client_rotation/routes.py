"""API-маршруты модуля «Ротация клиентов».

Все маршруты требуют: (1) валидной сессии - 401; (2) доступа к модулю
"client_rotation" - 403 (роль rop или analyst). tenant_id - ТОЛЬКО из сессии.
Видимость клиентов фильтруется по scope роли (own/team/domain/all).

GET  /api/client-rotation/clients      - список клиентов (по scope) + фильтры
GET  /api/client-rotation/managers     - принимающие менеджеры (активные employees)
POST /api/client-rotation/assignments  - назначить менеджера / задать статус
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.service import AuthContext
from core.client_rotation.export import export_assignments_by_manager
from core.client_rotation.export_managers import build_manager_export
from core.models import ClientHandover
from core.client_rotation.service import (
    company_in_tenant,
    employee_in_tenant,
    get_client_by_key,
    get_user_employee,
    list_clients,
    list_handovers,
    list_receiving_managers,
    upsert_assignment,
)
from core.db import get_db
from core.rbac.deps import require_module
from core.rbac.service import ModuleAccess

_ZIP_MEDIA = "application/zip"
_FNAME_BAD = re.compile(r'[\\/:*?"<>|]+')

router = APIRouter(prefix="/api/client-rotation", tags=["client_rotation"])

_MODULE = "client_rotation"


def _safe_filename(name: str) -> str:
    """Имя МОП -> безопасное имя файла (без запрещённых в ФС символов).

    Точку в конце ФИО («П.П.») не трогаем - расширение .xlsx идёт следом.
    """
    safe = _FNAME_BAD.sub("_", (name or "").strip())
    return (safe or "без_МОП")[:100]


def _parse_date(value: str | None) -> datetime | None:
    """'YYYY-MM-DD' -> datetime (начало дня). Некорректное/пустое -> None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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


@router.get("/client")
def api_client(
    key: str = Query(..., max_length=255),
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Карточка одной компании по source_key (переход к ЮЛ холдинга из карточки).

    Возвращает компанию даже если она скрыта из общего списка как член холдинга.
    """
    item = get_client_by_key(db, auth.tenant_id, key)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Компания не найдена")
    return item


@router.get("/managers")
def api_managers(
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[dict]:
    """Принимающие менеджеры: активные сотрудники арендатора с crm_name."""
    return list_receiving_managers(db, auth.tenant_id)


@router.get("/export")
def api_export(
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> Response:
    """Выгрузка в 1С: ZIP с отдельным xlsx (шаблон ДСП) на каждого принимающего МОП.

    Разворот холдингов сохранён. Имена файлов внутри архива - по ФИО МОП,
    при совпадении имён добавляется числовой суффикс.
    """
    files = export_assignments_by_manager(db, auth.tenant_id)
    buf = io.BytesIO()
    used: dict[str, int] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for mgr, data in files:
            base = _safe_filename(mgr)
            n = used.get(base, 0)
            used[base] = n + 1
            fname = f"{base}.xlsx" if n == 0 else f"{base} ({n}).xlsx"
            zf.writestr(fname, data)
    return Response(
        content=buf.getvalue(),
        media_type=_ZIP_MEDIA,
        headers={"Content-Disposition": "attachment; filename=1c_export.zip"},
    )


@router.post("/export-managers")
def api_export_managers(
    mode: str = Query("new", pattern="^(new|all)$"),
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> Response:
    """Выгрузка для МОП: ZIP с отдельным HTML-досье на каждого принимающего МОП.

    Скачивание ФИКСИРУЕТ передачу: вошедшие (ещё не переданные текущему МОП)
    компании пишутся в журнал передач и исключаются из следующих выгрузок.
    mode=new (по умолчанию) - только новые; mode=all - перевыпуск всех назначенных
    (новые записи журнала только по ещё не переданным, без дублей).

    Каждый файл - самодостаточная HTML-страница (инлайн-CSS) с клиентами одного
    принимающего менеджера. Имена файлов - по ФИО МОП, при совпадении числовой
    суффикс. ПД (контакты, ИНН) - только в границах арендатора и под доступом.
    """
    docs, to_mark = build_manager_export(db, auth.tenant_id, only_pending=(mode != "all"))
    # Фиксация передачи: по каждой ещё не переданной вошедшей компании - запись журнала.
    for company_id, employee_id, manager_name in to_mark:
        db.add(ClientHandover(
            tenant_id=auth.tenant_id,
            company_id=company_id,
            employee_id=employee_id,
            manager_name=manager_name,
            actor_user_id=auth.user_id,
            actor_login=auth.login,
        ))
    db.commit()
    buf = io.BytesIO()
    used: dict[str, int] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for mgr, html in docs:
            base = _safe_filename(mgr)
            n = used.get(base, 0)
            used[base] = n + 1
            fname = f"{base}.html" if n == 0 else f"{base} ({n}).html"
            zf.writestr(fname, html)
    return Response(
        content=buf.getvalue(),
        media_type=_ZIP_MEDIA,
        headers={
            "Content-Disposition": "attachment; filename=rotation_managers.zip",
            # Число файлов в архиве - чтобы фронт не скачивал пустой zip молча.
            "X-Export-Files": str(len(docs)),
        },
    )


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
    # bool - подкласс int, поэтому отсекаем явно: JSON true не должен стать id=1.
    if not isinstance(company_id, int) or isinstance(company_id, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="company_id обязателен (int)",
        )
    if not company_in_tenant(db, auth.tenant_id, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")

    # Частичное обновление: пишем только те поля, что реально пришли в теле.
    # Так инлайн-редактирование может менять статус ИЛИ принимающего по отдельности.
    patch: dict = {}
    if "assigned_to_employee_id" in body:
        emp_id = body.get("assigned_to_employee_id")
        if emp_id is not None:
            if not isinstance(emp_id, int) or isinstance(emp_id, bool) or not employee_in_tenant(db, auth.tenant_id, emp_id):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="assigned_to_employee_id неизвестен",
                )
        patch["assigned_to_employee_id"] = emp_id
    if "transfer_status" in body:
        patch["transfer_status"] = body.get("transfer_status") or None
    if "comment" in body:
        patch["comment"] = body.get("comment") or None

    assignment = upsert_assignment(db, auth.tenant_id, company_id=company_id, **patch)
    db.commit()
    return {
        "ok": True,
        "company_id": assignment.company_id,
        "assigned_to_employee_id": assignment.assigned_to_employee_id,
        "transfer_status": assignment.transfer_status,
    }


@router.get("/handovers")
def api_handovers(
    manager: str | None = Query(None, max_length=255),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    _access: ModuleAccess = Depends(require_module(_MODULE)),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[dict]:
    """Журнал передач арендатора (новые сверху). Фильтры: МОП и период from/to.

    from/to - 'YYYY-MM-DD'; to включается целиком (сдвиг на конец дня).
    """
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if dt is not None:
        dt = dt + timedelta(days=1)  # верхняя граница включительно (до конца дня)
    return list_handovers(db, auth.tenant_id, manager=manager or None, date_from=df, date_to=dt)
