"""Модели ядра платформы (SQLAlchemy 2.0, декларативный стиль).

Здесь живёт `Base.metadata` - единый источник схемы для Alembic
(см. migrations/env.py). Фаза 1.1 заводит минимум для входа:
tenants, users, sessions. Таблицы RBAC добавлены в 1.3.
Мастер-данные (employees, companies) добавлены в 1.4.

Инварианты (см. AI/platform/модель_данных.md, роли_и_доступ.md):
- tenant_id присутствует во всех бизнес-таблицах с первого дня;
- пароли хранятся только хешем (argon2), никогда в открытом виде;
- неактивных пользователей деактивируем (is_active=False), не удаляем.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from datetime import date

# JSON-поля: JSONB на PostgreSQL (прод), generic JSON на SQLite (тесты).
# Один экземпляр типа переиспользуется столбцами - это допустимо в SQLAlchemy.
_JSON = JSON().with_variant(JSONB, "postgresql")


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
    # При выдаче доступа ставится True: при первом входе платформа потребует
    # сменить временный пароль (добавочный номер) на постоянный.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=func.false()
    )
    # Дата последней смены пароля. nullable: у старых учёток ещё не заполнена,
    # выставляется при каждой смене (initial/self_change) через password_log.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    employee: Mapped[Employee | None] = relationship(back_populates="users")
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    user_roles: Mapped[list[UserRole]] = relationship(
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


# Типы событий журнала смены пароля. initial — пароль задан при выдаче доступа
# или создании учётки; self_change — пользователь сменил свой пароль сам.
PASSWORD_EVENT_VALUES = ("initial", "self_change")


class PasswordChangeLog(Base):
    """Журнал смены паролей (append-only, без FK-каскадов).

    По образцу vendor_audit_log. Значение пароля/хеша НИКОГДА не пишется —
    только факт: чей пароль, кто сменил, какого типа событие, когда.
    user_login/actor_login — снимки логинов на момент события: запись остаётся
    читаемой, даже если учётка позже удалена.
    """

    __tablename__ = "password_change_log"
    # Составной индекс под фактический запрос истории (tenant_id, user_id).
    # Явное имя совпадает с миграцией 0112 — иначе index=True даст автоимена и
    # autogenerate увидит ложный drift (как в employee_positions).
    __table_args__ = (
        Index("ix_password_change_log_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_login: Mapped[str] = mapped_column(String(150), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_login: Mapped[str] = mapped_column(String(150), nullable=False)
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


# --- Мастер-данные (Фаза 1.4): сотрудники и компании-клиенты ---
#
# Эти таблицы - ядро, на которое ссылаются модули (Продажи и т.д.).
# Данные описываются один раз; модуль ссылается по FK, не копирует.
# inn уникален в рамках tenant (ключ клиента, как в client-rotate).


class Employee(Base):
    """Сотрудник компании-арендатора (мастер-данные).

    ФИО хранятся тремя полями; full_name - Python-свойство для отображения.
    crm_name - псевдоним в CRM-выгрузках для маппинга.
    manager_id - самоссылка для иерархии подчинения.
    Уволенных деактивируем, не удаляем.
    """

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("tenant_id", "last_name", "first_name", "middle_name", name="uq_employees_tenant_fio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    last_name: Mapped[str] = mapped_column(String(150), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    crm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_personal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_work: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_extension: Mapped[str | None] = mapped_column(String(20), nullable=True)
    domain_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def full_name(self) -> str:
        parts = [self.last_name]
        if self.first_name:
            parts.append(self.first_name)
        if self.middle_name:
            parts.append(self.middle_name)
        return " ".join(parts)

    users: Mapped[list[User]] = relationship(back_populates="employee")


class EmployeePosition(Base):
    """Справочник должностей арендатора. Наполняется вручную через UI."""

    __tablename__ = "employee_positions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_emp_pos_tenant_name"),
        # Явное имя индекса под фактическое в БД (миграция 0108) - иначе
        # index=True даёт автоимя ix_employee_positions_tenant_id и autogenerate
        # видит ложный drift. Имя в стиле сокращений этой таблицы (uq_emp_pos_*).
        Index("ix_emp_pos_tenant_id", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Company(Base):
    """Компания-клиент (мастер-данные).

    source_key - стабильный ключ клиента из источника: ИНН (если есть) либо
    суррогат для клиентов без ИНН (в client-rotate - "|Имя|Менеджер"). Именно
    он уникален в рамках tenant и служит точкой связи для модулей. inn nullable:
    в выгрузках ИНН есть не у всех (~15% без него), но клиент - реальный.
    holding_id - текстовая метка холдинга для группировки (не FK,
    холдинг не самостоятельная сущность). is_holding_head отмечает
    головную компанию холдинга. ИНН - PII; доступ по роли (см. RBAC).
    """

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", name="uq_companies_tenant_source_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_key: Mapped[str] = mapped_column(String(150), nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    holding_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_holding_head: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# --- Вендоры (Фаза 1.6): справочник партнёров/вендоров ---
#
# portal_password_enc — зашифрованный Fernet-токен (core/vendors/crypto.py).
# В БД хранится только зашифрованный blob, открытый пароль никогда не пишется.
# Дистрибьюторы — отдельная таблица (в CSV было 5×5 колонок — нормализовано).

# Допустимые типы партнёрского статуса. Хранятся строкой — дополняемо без миграции.
VENDOR_STATUS_VALUES = ("active", "suspended", "revoked", "deauth", "closed", "none", "overdue")

# Решение по продлению статуса (null = не принято, yes = продлеваем, no = не продлеваем)
RENEWAL_DECISION_VALUES = ("yes", "no")

COMPANY_TYPE_VALUES = ("vendor", "distributor", "partner")


class Vendor(Base):
    """Вендор/дистрибьютор/партнёр. Один контрагент — одна строка в рамках tenant.

    company_type: vendor=производитель, distributor=дистрибьютор, partner=партнёр.
    categories — через запятую («ИБ, Сетевое»). status_type — тип статуса
    из каталога; status_text — человекочитаемый текст от вендора
    («Silver partner / Dealer»). portal_password_enc — Fernet-шифр.
    """

    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_vendors_tenant_name"),
        Index("ix_vendors_status_type", "status_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="vendor", server_default="vendor"
    )
    categories: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    renewal_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    partner_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    legal_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    directions: Mapped[str | None] = mapped_column(String(500), nullable=True)
    discount: Mapped[str | None] = mapped_column(Text, nullable=True)
    purchase_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    portal_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portal_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portal_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_contact: Mapped[str | None] = mapped_column(String(500), nullable=True)
    deal_registration: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mop_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    distributors: Mapped[list[VendorDistributor]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",
        order_by="VendorDistributor.sort_order",
    )


class VendorDistributor(Base):
    """Дистрибьютор вендора (у одного вендора может быть до 5).

    sort_order 1-5 отражает порядок из исходного CSV (Дистрибьютор 1..5).
    """

    __tablename__ = "vendor_distributors"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    vendor: Mapped[Vendor] = relationship(back_populates="distributors")


class VendorAuditLog(Base):
    """Аудит-лог операций над вендорами (append-only, без FK-каскадов).

    vendor_name и user_login — снимки имён на момент события: даже если вендор
    или пользователь удалены, история остаётся читаемой.
    action: create | update | delete
    field_name: название поля (только для action=update).
    """

    __tablename__ = "vendor_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vendor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_login: Mapped[str] = mapped_column(String(150), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


# --- RBAC (Фаза 1.3): доступ = (модули роли) × (область данных роли) ---
#
# Заметка о tenant_id: он несётся в `roles` (политика доступа у каждого
# арендатора своя). `modules` - платформенный КАТАЛОГ кода (какие модули
# вообще существуют), он один на всю установку, поэтому без tenant_id.
# Связки `user_roles`/`role_modules` tenant_id не дублируют - он однозначно
# выводится через user/role (не вводим избыточный ключ, способный рассинхрониться).
# См. AI/platform/модель_данных.md, роли_и_доступ.md.

# Допустимые области данных (scope) в порядке расширения прав.
# Совпадает с SCOPE_RANK в core/rbac/service.py - там единый источник ранга.
SCOPE_VALUES = ("own", "team", "domain", "all")


class Role(Base):
    """Роль внутри арендатора (mop, rop, analyst, security, domain_head).

    code уникален в рамках tenant. Сама роль не несёт scope - область данных
    задаётся при НАЗНАЧЕНИИ роли пользователю (user_roles.scope), потому что
    один и тот же `rop` для разных людей может означать разные команды.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )
    role_modules: Mapped[list[RoleModule]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class Module(Base):
    """Реестр модулей платформы (каталог кода). Глобальный, без tenant_id.

    is_enabled - подключён ли модуль на этой установке (выключенный не попадает
    в навигацию даже если роль его перечисляет). sort_order - порядок в меню.
    """

    __tablename__ = "modules"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    role_modules: Mapped[list[RoleModule]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )


class UserRole(Base):
    """Назначение роли пользователю с областью данных (scope).

    Один пользователь может иметь несколько ролей (кейс Первухина: rop+mop).
    Итоговый доступ - объединение прав ролей, по модулю берётся самый широкий
    scope (см. core/rbac/service.py). scope_ref уточняет границу team/domain.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Область данных: own/team/domain/all (SCOPE_VALUES). Хранится строкой -
    # дополняемо без миграции; валидность проверяет сервис/сид при записи.
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="own")
    scope_ref: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="user_roles")
    role: Mapped[Role] = relationship(back_populates="user_roles")


class RoleModule(Base):
    """Доступ роли к модулю (роль видит модуль в навигации и его API)."""

    __tablename__ = "role_modules"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(
        ForeignKey("modules.code", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped[Role] = relationship(back_populates="role_modules")
    module: Mapped[Module] = relationship(back_populates="role_modules")


# --- Ротация клиентов (модуль client_rotation, план 2026-06-30) ---
#
# Перенос инструмента client-rotate в платформу. Идентичность клиента -
# в companies (master-data, source_key). Здесь - три таблицы модуля,
# все ссылаются на companies.id (а не на текстовый ИНН), tenant_id везде.
#
# Разделение источников (как в client-rotate):
#  - client_rotation_data.summary - детерминированное саммари (из скоринга);
#    summaries.summary - LLM-саммари (анализ комментариев). Это РАЗНЫЕ поля.
#  - client_rotation_data.transfer_status - исходный статус из выгрузки;
#    assignments.transfer_status - ручной override РОПа (важнее исходного).
# PII: phone, email, contact_person, comments_corpus (комментарии менеджеров -
# контент третьих лиц). Доступ строго по роли/scope (см. RBAC).


class ClientRotationData(Base):
    """Расширенные данные клиента для ротации (1:1 к companies).

    Хранит всё, чего нет в companies: скоринг, признаки присутствия в ДСП/СП,
    «давность» контактов, контакты, обогащение из CRM. Переживает пересборку
    companies. Пересборка датасета перезаписывает эту таблицу; назначения и
    LLM-саммари живут отдельно (assignments, summaries).
    """

    __tablename__ = "client_rotation_data"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_crd_company"),
        Index("ix_crd_score", "score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # Текущий менеджер из выгрузки (строка-имя; не FK - может быть уволенный/внешний).
    current_manager: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Уровень клиента (Ключевой/Корпоративный/Малый бизнес/Микробизнес) - из выгрузки
    # УКБ. Используется чипом уровня в HTML-досье для МОП (export_managers.py).
    level: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_orphan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_sp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_dsp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    days_no_contact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_no_kp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_no_shipment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site: Mapped[str | None] = mapped_column(String(500), nullable=True)
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    turnover_json: Mapped[dict | list | None] = mapped_column(_JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    dsp_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    sp_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_corpus: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown_json: Mapped[dict | list | None] = mapped_column(_JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    holding_members_json: Mapped[dict | list | None] = mapped_column(_JSON, nullable=True)


class Assignment(Base):
    """Решение по ротации клиента: кому передать + ручной статус.

    Живёт отдельно от client_rotation_data, чтобы переживать пересборку датасета.
    assigned_to_employee_id - принимающий менеджер (FK employees, SET NULL: при
    деактивации/удалении сотрудника назначение остаётся, ссылка обнуляется).
    transfer_status здесь - ручной override РОПа (приоритетнее исходного).
    """

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_assignments_company"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    assigned_to_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow, onupdate=utcnow, server_default=func.now(),
    )


class Summary(Base):
    """LLM-саммари клиента (анализ комментариев) + проверенный контакт.

    Источник правды - data/analysis.json в client-rotate; здесь хранится
    применённый результат. Живёт отдельно от датасета (переживает пересборку).
    contact_name/contact_phone - проверенный контакт, приоритетнее авто-контакта.
    """

    __tablename__ = "summaries"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_summaries_company"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workstations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow, onupdate=utcnow, server_default=func.now(),
    )


class ClientHandover(Base):
    """Журнал передач клиента принимающему МОП (append-only).

    Одна строка - факт состоявшейся передачи (выгрузка для МОП) компании
    конкретному принимающему менеджеру в конкретный момент. Источник правды
    для (а) инкрементальной выгрузки (исключаем уже переданных текущему МОП)
    и (б) аудита «какой клиент → какому МОП → когда → кто выгрузил».

    Append-only, без UNIQUE по company: компания может быть передана несколько
    раз (например, при переназначении другому МОП) - это история. Снимки
    manager_name/actor_login переживают изменение/удаление сотрудника и учётки.
    """

    __tablename__ = "client_handovers"
    __table_args__ = (
        Index("ix_handovers_tenant_company", "tenant_id", "company_id"),
        Index("ix_handovers_tenant_date", "tenant_id", "handed_over_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    manager_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handed_over_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow, server_default=func.now(),
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_login: Mapped[str | None] = mapped_column(String(150), nullable=True)
