"""Аудит-лог операций над вендорами.

Пишем в vendor_audit_log при каждом create/update/delete.
Читаем с фильтром по роли: маркетолог — только свои, аналитик — все.

Правила чувствительных полей:
- portal_password: не пишем значение, только факт ([задан] / [удалён]).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import Vendor, VendorAuditLog

# Поля, чьи значения не логируются напрямую (только факт изменения).
_SENSITIVE = frozenset({"portal_password"})

# Все поля, за которыми следим при update.
_TRACKED = (
    "company_type", "name", "categories", "status_type", "status_text",
    "valid_until", "partner_id", "legal_entity", "directions", "discount",
    "purchase_method", "portal_url", "portal_login", "portal_password",
    "vendor_contact", "deal_registration", "mop_comments",
)

# Отображаемые имена полей для UI.
FIELD_LABELS: dict[str, str] = {
    "company_type": "Тип",
    "name": "Название",
    "categories": "Категории",
    "status_type": "Статус (тип)",
    "status_text": "Статус (текст)",
    "valid_until": "Действует до",
    "partner_id": "Partner ID",
    "legal_entity": "Юрлицо",
    "directions": "Направления",
    "discount": "Скидка",
    "purchase_method": "Схема закупки",
    "portal_url": "URL портала",
    "portal_login": "Логин портала",
    "portal_password": "Пароль портала",
    "vendor_contact": "Контакт вендора",
    "deal_registration": "Deal registration",
    "mop_comments": "Заметки МОП",
}


def capture_state(vendor: Vendor) -> dict[str, str | None]:
    """Снимок отслеживаемых полей вендора (вызывать ДО update_vendor)."""
    state: dict[str, str | None] = {}
    for field in _TRACKED:
        if field == "portal_password":
            state[field] = "[задан]" if vendor.portal_password_enc else None
        else:
            val = getattr(vendor, field, None)
            state[field] = str(val) if val is not None else None
    return state


def _new_display(field: str, value: object) -> str | None:
    """Отображаемое значение после изменения."""
    if field == "portal_password":
        return "[задан]" if value else "[удалён]"
    return str(value) if value is not None else None


def log_create(
    db: DBSession,
    *,
    tenant_id: int,
    vendor: Vendor,
    user_id: int,
    user_login: str,
) -> None:
    db.add(VendorAuditLog(
        tenant_id=tenant_id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        user_id=user_id,
        user_login=user_login,
        action="create",
    ))


def log_update(
    db: DBSession,
    *,
    tenant_id: int,
    vendor: Vendor,
    user_id: int,
    user_login: str,
    old_state: dict[str, str | None],
    body: dict,
    allowed: frozenset[str],
) -> None:
    """Записать по одной строке на каждое изменившееся поле."""
    for field in _TRACKED:
        if field not in body or field not in allowed:
            continue
        new_val = _new_display(field, body[field])
        old_val = old_state.get(field)
        if new_val == old_val:
            continue
        db.add(VendorAuditLog(
            tenant_id=tenant_id,
            vendor_id=vendor.id,
            vendor_name=vendor.name,
            user_id=user_id,
            user_login=user_login,
            action="update",
            field_name=field,
            old_value=old_val,
            new_value=new_val,
        ))


def log_delete(
    db: DBSession,
    *,
    tenant_id: int,
    vendor: Vendor,
    user_id: int,
    user_login: str,
) -> None:
    db.add(VendorAuditLog(
        tenant_id=tenant_id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        user_id=user_id,
        user_login=user_login,
        action="delete",
    ))


def get_audit(
    db: DBSession,
    *,
    tenant_id: int,
    vendor_id: int,
    user_id: int | None = None,
) -> list[dict]:
    """История правок вендора. user_id=None — все, иначе только записи этого юзера."""
    stmt = (
        select(VendorAuditLog)
        .where(
            VendorAuditLog.tenant_id == tenant_id,
            VendorAuditLog.vendor_id == vendor_id,
        )
        .order_by(VendorAuditLog.created_at.desc())
    )
    if user_id is not None:
        stmt = stmt.where(VendorAuditLog.user_id == user_id)

    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "field_name": r.field_name,
            "field_label": FIELD_LABELS.get(r.field_name or "", r.field_name or ""),
            "old_value": r.old_value,
            "new_value": r.new_value,
            "user_login": r.user_login,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
