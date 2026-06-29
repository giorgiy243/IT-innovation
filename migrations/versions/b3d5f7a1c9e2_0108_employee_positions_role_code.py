"""0108 - справочник должностей + role_code на сотруднике

Revision ID: b3d5f7a1c9e2
Revises: f7a3c8e1d2b5
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3d5f7a1c9e2"
down_revision = "f7a3c8e1d2b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_emp_pos_tenant_name"),
    )
    op.create_index("ix_emp_pos_tenant_id", "employee_positions", ["tenant_id"])

    op.add_column("employees", sa.Column("role_code", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "role_code")
    op.drop_index("ix_emp_pos_tenant_id", table_name="employee_positions")
    op.drop_table("employee_positions")
