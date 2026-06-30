"""0112 - password_changed_at на users + журнал смены паролей

Revision ID: a3f9c1e7b4d2
Revises: f2c370ba2400
Create Date: 2026-06-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f9c1e7b4d2"
down_revision = "f2c370ba2400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Журнал — append-only, без FK-каскадов: удалённые учётки должны оставаться
    # в истории (user_login/actor_login хранят снимок логина). Значение пароля
    # не хранится никогда.
    op.create_table(
        "password_change_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.Integer, nullable=True),
        sa.Column("user_login", sa.String(150), nullable=False),
        sa.Column("actor_user_id", sa.Integer, nullable=True),
        sa.Column("actor_login", sa.String(150), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_password_change_log_tenant_user",
        "password_change_log",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_change_log_tenant_user", table_name="password_change_log")
    op.drop_table("password_change_log")
    op.drop_column("users", "password_changed_at")
