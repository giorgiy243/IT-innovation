"""Модели ядра платформы (SQLAlchemy 2.0, декларативный стиль).

Здесь живёт `Base.metadata` - единый источник схемы для Alembic
(см. migrations/env.py). Фаза 1.1 заводит минимум для входа:
tenants, users, sessions. Таблицы RBAC и мастер-данных добавляются
в 1.3-1.4 в этот же `Base`.

Инварианты (см. AI/platform/модель_данных.md, роли_и_доступ.md):
- tenant_id присутствует во всех бизнес-таблицах с первого дня;
- пароли хранятся только хешем (argon2), никогда в открытом виде;
- неактивных пользователей деактивируем (is_active=False), не удаляем.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Текущее время в UTC (timezone-aware). Единая точка для дефолтов."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Общий декларативный базовый класс. Его metadata видит Alembic."""


class Tenant(Base):
    """Организация-арендатор. На старте одна строка (АйТек).

    Все бизнес-таблицы ссылаются на tenant_id - мультиарендность
    заложена с первого дня (заложить, не строить).
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    users: Mapped[list[User]] = relationship(back_populates="tenant")


class User(Base):
    """Учётная запись для входа в платформу.

    login уникален в рамках tenant. password_hash - argon2, не пароль.
    Связь с employees (мастер-данные) появится в 1.4.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "login", name="uq_users_tenant_login"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    login: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """Серверная сессия (хранится в БД, не в куках клиента).

    Клиенту в httponly-куке отдаётся непредсказуемый сырой токен; в БД лежит
    только его sha256-хеш (token_hash) - утечка БД не даёт угнать сессию.
    tenant_id фиксируется в момент входа и в дальнейшем подставляется
    ядром ПРИНУДИТЕЛЬНО из сессии - никогда не приходит от клиента.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    # token_hash - первичный ключ: sha256-хекс сырого токена (64 символа).
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")
