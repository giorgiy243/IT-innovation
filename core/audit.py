"""Аудит-лог (append-only).

Кто, что, когда сделал. Записи не редактируются и не удаляются. Доступ к чтению -
роль ИБ/аудит. В meta не дублировать избыточный PII - хранить ключ-ссылку (company:<inn>),
а не весь профиль (см. AI/platform/роли_и_доступ.md).

Скелет подфазы 0.3. Реализация - Фаза 1.
"""


def log(tenant_id: str, user_id: str, action: str,
        entity: str | None = None, meta: dict | None = None) -> None:
    """Записать действие в аудит-лог. Реализация - Фаза 1."""
    raise NotImplementedError("Фаза 1: append-only запись в audit_log")
