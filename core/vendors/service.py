"""Сервисный слой: операции с вендорами.

Все функции принимают tenant_id явно - он должен приходить только из
серверной сессии (AuthContext.tenant_id), не из запроса клиента.
"""
from __future__ import annotations

from datetime import date as date_type, datetime, timezone
from dateutil.relativedelta import relativedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import Vendor
from core.vendors.crypto import decrypt, encrypt

# Поля, которые РОП может редактировать (только контакты и сделки)
ROP_ALLOWED_FIELDS: frozenset[str] = frozenset({"vendor_contact", "deal_registration"})

# Все редактируемые поля (без id, tenant_id, created_at, updated_at)
ALL_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "company_type", "name", "categories", "status_type", "status_text", "valid_until",
    "renewal_decision",
    "partner_id", "legal_entity", "directions", "discount", "purchase_method",
    "portal_url", "portal_login", "portal_password",
    "vendor_contact", "deal_registration", "mop_comments",
})


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
    check_and_update_status(v)
    return {
        "id": v.id,
        "name": v.name,
        "company_type": v.company_type,
        "categories": v.categories,
        "status_type": v.status_type,
        "status_text": v.status_text,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "has_warning": should_show_warning(v),
        "directions": v.directions,
        "partner_id": v.partner_id,
    }


def _apply_fields(v: Vendor, data: dict, allowed: frozenset[str]) -> None:
    """Применить поля из data к объекту вендора, только те что в allowed."""
    for field, value in data.items():
        if field not in allowed:
            continue
        if field == "portal_password":
            v.portal_password_enc = encrypt(value) if value else None
        elif field == "valid_until":
            v.valid_until = date_type.fromisoformat(value) if value else None
        else:
            setattr(v, field, value)


def create_vendor(db: DBSession, tenant_id: int, data: dict) -> Vendor:
    """Создать нового вендора. data должен содержать name."""
    v = Vendor(tenant_id=tenant_id, name=data["name"])
    _apply_fields(v, data, ALL_EDITABLE_FIELDS)
    db.add(v)
    db.flush()
    return v


def update_vendor(
    db: DBSession, vendor: Vendor, data: dict, *, allowed: frozenset[str]
) -> Vendor:
    """Обновить вендора. allowed ограничивает редактируемые поля."""
    _apply_fields(vendor, data, allowed)
    db.flush()
    return vendor


def delete_vendor(db: DBSession, vendor: Vendor) -> None:
    """Удалить вендора (cascade удалит distributors)."""
    db.delete(vendor)
    db.flush()


def check_and_update_status(v: Vendor) -> None:
    """Проверить и обновить статус вендора если срок истек.

    Логика:
    - Если valid_until < сегодня и renewal_decision != 'yes' → status_type = 'overdue'
    """
    if not v.valid_until:
        return
    today = date_type.today()
    if v.valid_until < today and v.renewal_decision != "yes":
        v.status_type = "overdue"


def should_show_warning(v: Vendor) -> bool:
    """Вернуть True если нужно показать иконку (!) для маркетолога/аналитика.

    Логика:
    - Показываем (!) если: valid_until не пуст И
      - (статус 'overdue' И renewal_decision != 'no') ИЛИ
      - (valid_until < сегодня + 30 дней И status_type != 'overdue' И renewal_decision != 'yes')
    """
    if not v.valid_until:
        return False
    today = date_type.today()
    warning_date = today + relativedelta(months=1)

    # Если просрочен и не выбран "не продлеваем" - показываем иконку
    if v.status_type == "overdue" and v.renewal_decision != "no":
        return True

    # Если до истечения менее месяца (и не просрочен) - показываем иконку
    if v.valid_until <= warning_date and v.status_type != "overdue":
        return True

    return False


def vendor_to_detail(v: Vendor) -> dict:
    """Полный словарь вендора; пароль портала расшифрован."""
    check_and_update_status(v)
    return {
        "id": v.id,
        "name": v.name,
        "company_type": v.company_type,
        "categories": v.categories,
        "status_type": v.status_type,
        "status_text": v.status_text,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "renewal_decision": v.renewal_decision,
        "has_warning": should_show_warning(v),
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
