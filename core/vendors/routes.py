"""API-маршруты модуля вендоров (Фаза 1.6.3).

Все маршруты требуют: (1) валидной сессии - 401, (2) доступа к модулю
"sales" - 403. tenant_id берётся ТОЛЬКО из серверной сессии.

GET /api/vendors            - список вендоров с фильтрами + список категорий
GET /api/vendors/{id}       - полная карточка вендора (пароль расшифрован)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.service import AuthContext
from core.db import get_db
from core.rbac.deps import require_module
from core.rbac.service import ModuleAccess
from core.vendors.service import (
    get_vendor,
    list_vendors,
    unique_categories,
    vendor_to_detail,
    vendor_to_list_item,
)

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


@router.get("")
def api_list(
    q: str | None = Query(None, max_length=150),
    category: str | None = Query(None, max_length=100),
    status_type: str | None = Query(None, max_length=20),
    _access: ModuleAccess = Depends(require_module("sales")),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Список вендоров с опциональными фильтрами.

    Возвращает: vendors (отфильтрованный список), categories (все уникальные для
    выпадашки фильтра), total (кол-во строк в выдаче).
    """
    vendors = list_vendors(db, auth.tenant_id, q=q, category=category, status_type=status_type)
    cats = unique_categories(db, auth.tenant_id)
    return {
        "vendors": [vendor_to_list_item(v) for v in vendors],
        "categories": cats,
        "total": len(vendors),
    }


@router.get("/{vendor_id}")
def api_detail(
    vendor_id: int,
    _access: ModuleAccess = Depends(require_module("sales")),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Полная карточка вендора. Пароль портала расшифровывается перед отдачей."""
    vendor = get_vendor(db, auth.tenant_id, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вендор не найден")
    return vendor_to_detail(vendor)
