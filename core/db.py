"""Подключение к БД и tenant-контекст.

Инвариант (см. AI/platform/роли_и_доступ.md): tenant_id подставляется ядром
ПРИНУДИТЕЛЬНО из сессии, никогда не приходит от клиента - иначе утечка между арендаторами.

Скелет подфазы 0.3 - без реализации. Реализация (SQLAlchemy engine/session) - Фаза 1.
"""


def get_engine(database_url: str):
    """Создать движок БД по DATABASE_URL. Реализация - Фаза 1."""
    raise NotImplementedError("Фаза 1: инициализация SQLAlchemy engine")


def session_scope(tenant_id: str):
    """Сессия БД с принудительным tenant-контекстом. Реализация - Фаза 1."""
    raise NotImplementedError("Фаза 1: сессия с tenant_id из аутентификации")
