"""0104 - мастер-данные: employees, companies, users.employee_id

Revision ID: c3e7f01a2d88
Revises: 4d2a9f3b1c77
Create Date: 2026-06-27

Порядок: employees создаётся ДО ALTER TABLE users (FK users.employee_id -> employees.id).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3e7f01a2d88"
down_revision = "4d2a9f3b1c77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("crm_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "full_name", name="uq_employees_tenant_name"),
    )
    op.create_index("ix_employees_tenant_id", "employees", ["tenant_id"])

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("inn", sa.String(12), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("segment", sa.String(100), nullable=True),
        sa.Column("holding_id", sa.String(255), nullable=True),
        sa.Column("is_holding_head", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "inn", name="uq_companies_tenant_inn"),
    )
    op.create_index("ix_companies_tenant_id", "companies", ["tenant_id"])

    op.add_column(
        "users",
        sa.Column("employee_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_users_employee_id", "users", ["employee_id"])
    op.create_foreign_key(
        "fk_users_employee_id",
        "users", "employees",
        ["employee_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_employee_id", "users", type_="foreignkey")
    op.drop_index("ix_users_employee_id", table_name="users")
    op.drop_column("users", "employee_id")

    op.drop_index("ix_companies_tenant_id", table_name="companies")
    op.drop_table("companies")

    op.drop_index("ix_employees_tenant_id", table_name="employees")
    op.drop_table("employees")
