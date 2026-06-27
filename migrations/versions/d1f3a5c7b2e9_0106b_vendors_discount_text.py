"""0106b - vendors.discount: varchar(100) -> text (скидки бывают длинными)

Revision ID: d1f3a5c7b2e9
Revises: b9d4e2f7a3c1
Create Date: 2026-06-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1f3a5c7b2e9"
down_revision = "b9d4e2f7a3c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("vendors", "discount", type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("vendors", "discount", type_=sa.String(100), existing_nullable=True)
