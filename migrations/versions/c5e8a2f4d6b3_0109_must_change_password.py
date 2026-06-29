"""0109 - флаг must_change_password на пользователе

Revision ID: c5e8a2f4d6b3
Revises: b3d5f7a1c9e2
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5e8a2f4d6b3"
down_revision = "b3d5f7a1c9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
