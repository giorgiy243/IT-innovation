"""API-маршруты модуля вендоров (Фаза 1.6.3-1.6.4).

Все маршруты требуют: (1) валидной сессии - 401, (2) доступа к модулю
"sales" - 403. tenant_id берётся ТОЛЬКО из серверной сессии.

GET    /api/vendors         - список вендоров с фильтрами + список категорий
GET    /api/vendors/{id}    - полная карточка вендора (пароль расшифрован)
POST   /api/vendors         - создать вендора (маркетолог, аналитик)
PATCH  /api/vendors/{id}    - редактировать: маркетолог/аналитик=все поля,
                              rop=только vendor_contact и deal_registration
DELETE /api/vendors/{id}    - удалить вендора (маркетолог, аналитик)
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.service import AuthContext
from core.db import get_db
from core.rbac.deps import get_access, require_module
from core.rbac.service import AccessProfile, ModuleAccess
from core.vendors.service import (
    ALL_EDITABLE_FIELDS,
    ROP_ALLOWED_FIELDS,
    create_vendor,
    delete_vendor,
    get_vendor,
    list_vendors,
    unique_categories,
    update_vendor,
    vendor_to_detail,
    vendor_to_list_item,
)

# Роли с полным правом записи (все поля)
_FULL_WRITE = frozenset({"marketer", "analyst"})
# Роли с ограниченным правом записи (только vendor_contact, deal_registration)
_ROP_WRITE = frozenset({"rop"})


def _check_write(profile: AccessProfile, *, full_only: bool = False) -> frozenset[str]:
    """Вернуть набор разрешённых полей для записи или поднять 403.

    full_only=True - только маркетолог/аналитик (создание, удаление).
    """
    if _FULL_WRITE.intersection(profile.role_codes):
        return ALL_EDITABLE_FIELDS
    if not full_only and _ROP_WRITE.intersection(profile.role_codes):
        return ROP_ALLOWED_FIELDS
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")

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


@router.post("", status_code=status.HTTP_201_CREATED)
def api_create(
    body: dict = Body(...),
    _access: ModuleAccess = Depends(require_module("sales")),
    profile: AccessProfile = Depends(get_access),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Создать вендора. Доступно: маркетолог, аналитик."""
    _check_write(profile, full_only=True)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name обязателен")
    try:
        vendor = create_vendor(db, auth.tenant_id, {**body, "name": name})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Вендор с таким именем уже существует")
    db.refresh(vendor)
    return vendor_to_detail(vendor)


@router.patch("/{vendor_id}")
def api_update(
    vendor_id: int,
    body: dict = Body(default={}),
    _access: ModuleAccess = Depends(require_module("sales")),
    profile: AccessProfile = Depends(get_access),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict:
    """Обновить вендора. Маркетолог/аналитик — все поля. РОП — только контакты и сделки."""
    allowed = _check_write(profile)
    vendor = get_vendor(db, auth.tenant_id, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вендор не найден")
    try:
        update_vendor(db, vendor, body, allowed=allowed)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Вендор с таким именем уже существует")
    db.refresh(vendor)
    return vendor_to_detail(vendor)


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete(
    vendor_id: int,
    _access: ModuleAccess = Depends(require_module("sales")),
    profile: AccessProfile = Depends(get_access),
    auth: AuthContext = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> None:
    """Удалить вендора. Доступно: маркетолог, аналитик."""
    _check_write(profile, full_only=True)
    vendor = get_vendor(db, auth.tenant_id, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вендор не найден")
    delete_vendor(db, vendor)
    db.commit()
