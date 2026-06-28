"""Сервисный слой: операции с вендорами.

Все функции принимают tenant_id явно - он должен приходить только из
серверной сессии (AuthContext.tenant_id), не из запроса клиента.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import Vendor
from core.vendors.crypto import decrypt


def list_vendors(
    db: DBSession,
    tenant_id: int,
    *,
    q: str | None = None,
    category: str | None = None,
    status_type: str | None = None,
) -> list[Vendor]:
    """Список вендоров арендатора с опциональной фильтрацией, сортировка по имени."""
    stmt = (
        select(Vendor)
        .where(Vendor.tenant_id == tenant_id)
        .order_by(Vendor.name)
    )
    if q:
        stmt = stmt.where(Vendor.name.ilike(f"%{q}%"))
    if category:
        stmt = stmt.where(Vendor.categories.ilike(f"%{category}%"))
    if status_type:
        stmt = stmt.where(Vendor.status_type == status_type)
    return list(db.execute(stmt).scalars().all())


def get_vendor(db: DBSession, tenant_id: int, vendor_id: int) -> Vendor | None:
    """Вендор по id с проверкой tenant. None если не найден или чужой."""
    v = db.get(Vendor, vendor_id)
    if v is None or v.tenant_id != tenant_id:
        return None
    return v


def unique_categories(db: DBSession, tenant_id: int) -> list[str]:
    """Уникальные отдельные категории для фильтра (поле categories - через запятую)."""
    raws = list(
        db.execute(
            select(Vendor.categories)
            .where(Vendor.tenant_id == tenant_id)
            .where(Vendor.categories.is_not(None))
            .distinct()
        ).scalars().all()
    )
    cats: set[str] = set()
    for raw in raws:
        for c in raw.split(","):
            c = c.strip()
            if c:
                cats.add(c)
    return sorted(cats)


def vendor_to_list_item(v: Vendor) -> dict:
    """Сокращённый словарь вендора для строки таблицы (без чувствительных данных)."""
    return {
        "id": v.id,
        "name": v.name,
        "categories": v.categories,
        "status_type": v.status_type,
        "status_text": v.status_text,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "directions": v.directions,
        "partner_id": v.partner_id,
    }


def vendor_to_detail(v: Vendor) -> dict:
    """Полный словарь вендора; пароль портала расшифрован."""
    return {
        "id": v.id,
        "name": v.name,
        "categories": v.categories,
        "status_type": v.status_type,
        "status_text": v.status_text,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "partner_id": v.partner_id,
        "legal_entity": v.legal_entity,
        "directions": v.directions,
        "discount": v.discount,
        "purchase_method": v.purchase_method,
        "portal_url": v.portal_url,
        "portal_login": v.portal_login,
        "portal_password": (
            decrypt(v.portal_password_enc) if v.portal_password_enc else None
        ),
        "vendor_contact": v.vendor_contact,
        "deal_registration": v.deal_registration,
        "mop_comments": v.mop_comments,
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat(),
        "distributors": [
            {
                "sort_order": d.sort_order,
                "name": d.name,
                "contact": d.contact,
                "email": d.email,
                "phone": d.phone,
                "note": d.note,
            }
            for d in v.distributors
        ],
    }
