"""Журнал смены паролей: запись событий и чтение истории.

Чистый слой без FastAPI (по образцу core/vendors/audit.py). В журнал пишется
только факт смены — значение пароля/хеша не передаётся сюда и не хранится.

Единый источник даты: log_password_change одновременно добавляет строку журнала
и проставляет user.password_changed_at, чтобы дата последней смены и история
никогда не разъезжались.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.models import PasswordChangeLog, User, utcnow

# Отображаемые названия событий для UI.
EVENT_LABELS: dict[str, str] = {
    "initial": "Выдан пароль",
    "self_change": "Смена пользователем",
}


def log_password_change(
    db: DBSession,
    *,
    tenant_id: int,
    user: User,
    actor_user_id: int | None,
    actor_login: str,
    event: str,
) -> None:
    """Записать событие смены пароля и обновить дату последней смены.

    user — чья учётка (берём из неё login и проставляем password_changed_at).
    actor — кто выполнил смену (сам пользователь или админ). При initial актор
    обычно админ, при self_change актор == user.
    Не коммитит — фиксация остаётся за вызывающим эндпоинтом.
    """
    now = utcnow()
    user.password_changed_at = now
    db.add(PasswordChangeLog(
        tenant_id=tenant_id,
        user_id=user.id,
        user_login=user.login,
        actor_user_id=actor_user_id,
        actor_login=actor_login,
        event=event,
        created_at=now,
    ))


def get_password_history(
    db: DBSession,
    *,
    tenant_id: int,
    user_id: int,
) -> list[dict]:
    """История смены пароля учётки (новые сверху)."""
    rows = db.execute(
        select(PasswordChangeLog)
        .where(
            PasswordChangeLog.tenant_id == tenant_id,
            PasswordChangeLog.user_id == user_id,
        )
        .order_by(PasswordChangeLog.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id,
            "event": r.event,
            "event_label": EVENT_LABELS.get(r.event, r.event),
            "actor_login": r.actor_login,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
