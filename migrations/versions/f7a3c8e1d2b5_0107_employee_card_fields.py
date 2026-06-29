"""0107 - карточка сотрудника: ФИО, контакты, должность, руководитель

Revision ID: f7a3c8e1d2b5
Revises: 2e9359242a33
Create Date: 2026-06-29

full_name заменяется тремя полями (last_name, first_name, middle_name).
Существующие значения full_name копируются в last_name при апгрейде.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a3c8e1d2b5"
down_revision = "2e9359242a33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Новые колонки (nullable, чтобы не сломать существующие строки)
    op.add_column("employees", sa.Column("last_name", sa.String(150), nullable=True))
    op.add_column("employees", sa.Column("first_name", sa.String(100), nullable=True))
    op.add_column("employees", sa.Column("middle_name", sa.String(100), nullable=True))
    op.add_column("employees", sa.Column("position", sa.String(255), nullable=True))
    op.add_column("employees", sa.Column("phone_personal", sa.String(50), nullable=True))
    op.add_column("employees", sa.Column("phone_work", sa.String(50), nullable=True))
    op.add_column("employees", sa.Column("phone_extension", sa.String(20), nullable=True))
    op.add_column("employees", sa.Column("domain_name", sa.String(255), nullable=True))
    op.add_column("employees", sa.Column("manager_id", sa.Integer(), nullable=True))

    # 2. Переносим данные: full_name → last_name
    op.execute("UPDATE employees SET last_name = full_name")

    # 3. Делаем last_name NOT NULL
    op.alter_column("employees", "last_name", nullable=False)

    # 4. Удаляем старый уникальный индекс и колонку full_name
    op.drop_constraint("uq_employees_tenant_name", "employees", type_="unique")
    op.drop_column("employees", "full_name")

    # 5. Новый уникальный индекс по ФИО
    op.create_unique_constraint(
        "uq_employees_tenant_fio",
        "employees",
        ["tenant_id", "last_name", "first_name", "middle_name"],
    )

    # 6. FK и индекс для manager_id (самоссылка)
    op.create_foreign_key(
        "fk_employees_manager_id",
        "employees", "employees",
        ["manager_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_employees_manager_id", "employees", ["manager_id"])


def downgrade() -> None:
    op.drop_index("ix_employees_manager_id", table_name="employees")
    op.drop_constraint("fk_employees_manager_id", "employees", type_="foreignkey")
    op.drop_constraint("uq_employees_tenant_fio", "employees", type_="unique")

    # Восстанавливаем full_name из last_name
    op.add_column("employees", sa.Column("full_name", sa.String(255), nullable=True))
    op.execute("UPDATE employees SET full_name = last_name")
    op.alter_column("employees", "full_name", nullable=False)

    op.create_unique_constraint("uq_employees_tenant_name", "employees", ["tenant_id", "full_name"])

    op.drop_column("employees", "last_name")
    op.drop_column("employees", "first_name")
    op.drop_column("employees", "middle_name")
    op.drop_column("employees", "position")
    op.drop_column("employees", "phone_personal")
    op.drop_column("employees", "phone_work")
    op.drop_column("employees", "phone_extension")
    op.drop_column("employees", "domain_name")
    op.drop_column("employees", "manager_id")
