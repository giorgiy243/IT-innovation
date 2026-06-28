"""0164 - company_type в vendors + таблица аудит-лога

Revision ID: e2a4c6f8d0b1
Revises: d1f3a5c7b2e9
Create Date: 2026-06-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2a4c6f8d0b1"
down_revision = "d1f3a5c7b2e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column(
            "company_type",
            sa.String(20),
            nullable=False,
            server_default="vendor",
        ),
    )

    # Аудит-лог — append-only, без FK-каскадов: удалённые вендоры/юзеры
    # должны оставаться в истории (vendor_name/user_login хранят снимок имени).
    op.create_table(
        "vendor_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("vendor_id", sa.Integer, nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer, nullable=True),
        sa.Column("user_login", sa.String(150), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_vendor_audit_log_tenant_vendor",
        "vendor_audit_log",
        ["tenant_id", "vendor_id"],
    )
    op.create_index(
        "ix_vendor_audit_log_user_id",
        "vendor_audit_log",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vendor_audit_log_user_id", table_name="vendor_audit_log")
    op.drop_index("ix_vendor_audit_log_tenant_vendor", table_name="vendor_audit_log")
    op.drop_table("vendor_audit_log")
    op.drop_column("vendors", "company_type")
