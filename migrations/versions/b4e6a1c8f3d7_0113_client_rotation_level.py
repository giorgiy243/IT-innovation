"""0113 - level на client_rotation_data (уровень клиента для досье МОП)

Revision ID: b4e6a1c8f3d7
Revises: a3f9c1e7b4d2
Create Date: 2026-06-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4e6a1c8f3d7"
down_revision = "a3f9c1e7b4d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "client_rotation_data",
        sa.Column("level", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("client_rotation_data", "level")
