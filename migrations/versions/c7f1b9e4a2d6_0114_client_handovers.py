"""0114 - client_handovers (журнал передач клиентов МОП)

Revision ID: c7f1b9e4a2d6
Revises: b4e6a1c8f3d7
Create Date: 2026-06-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7f1b9e4a2d6"
down_revision = "b4e6a1c8f3d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_handovers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.Column("manager_name", sa.String(255), nullable=True),
        sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_login", sa.String(150), nullable=True),
    )
    op.create_index("ix_client_handovers_tenant_id", "client_handovers", ["tenant_id"])
    op.create_index("ix_handovers_tenant_company", "client_handovers", ["tenant_id", "company_id"])
    op.create_index("ix_handovers_tenant_date", "client_handovers", ["tenant_id", "handed_over_at"])


def downgrade() -> None:
    op.drop_index("ix_handovers_tenant_date", table_name="client_handovers")
    op.drop_index("ix_handovers_tenant_company", table_name="client_handovers")
    op.drop_index("ix_client_handovers_tenant_id", table_name="client_handovers")
    op.drop_table("client_handovers")
