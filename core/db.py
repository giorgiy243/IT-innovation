"""Подключение к БД и сессии SQLAlchemy.

Инвариант (см. AI/platform/роли_и_доступ.md): tenant_id подставляется ядром
ПРИНУДИТЕЛЬНО из аутентификации, никогда не приходит от клиента - иначе
утечка между арендаторами. В 1.1 точка принуждения - зависимость доступа
(core.auth.deps): она достаёт tenant_id из серверной сессии и отдаёт его
запросам. Запросы модулей обязаны фильтровать по этому tenant_id.
Полное принуждение на уровне БД (RLS) - отдельная задача более поздней фазы.

DATABASE_URL читается из .env (формат postgresql+psycopg://...).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# .env из корня репозитория - чтобы CLI/скрипты тоже видели DATABASE_URL.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_database_url() -> str:
    """DATABASE_URL из окружения. Без него работать нельзя - явная ошибка."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL не задан. Скопируй .env.example -> .env и заполни."
        )
    return url


def get_engine() -> Engine:
    """Ленивый singleton-движок БД. pool_pre_ping - переживать обрывы соединения."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    """Ленивая фабрика сессий, привязанная к движку."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакционная сессия: commit при успехе, rollback при исключении.

    Для скриптов и сервисного слоя. tenant_id здесь не подставляется -
    его передаёт вызывающий код, получив из аутентификации (см. модуль docstring).
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на время запроса.

    Без авто-commit - решение фиксировать транзакцию принимает эндпоинт явно,
    чтобы случайные чтения не превращались в запись.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
