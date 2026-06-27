"""0103 rbac: roles, modules, user_roles, role_modules

Revision ID: 4d2a9f3b1c77
Revises: 28ccc0ec7375
Create Date: 2026-06-27 10:00:00.000000

Доступ = (модули роли) × (область данных роли). Права хранятся данными,
а не кодом (см. AI/platform/роли_и_доступ.md). Таблицы добавляются в общий
Base поверх 1.1 (tenants/users/sessions).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4d2a9f3b1c77"
down_revision: Union[str, Sequence[str], None] = "28ccc0ec7375"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # roles - роли арендатора (code уникален в рамках tenant).
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_code"),
    )
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"], unique=False)

    # modules - платформенный каталог модулей (глобальный, без tenant_id).
    op.create_table(
        "modules",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    # user_roles - назначение роли пользователю с областью данных (scope).
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("scope_ref", sa.String(length=150), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index(
        op.f("ix_user_roles_role_id"), "user_roles", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_roles_user_id"), "user_roles", ["user_id"], unique=False
    )

    # role_modules - доступ роли к модулю (составной PK).
    op.create_table(
        "role_modules",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("module_code", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["module_code"], ["modules.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "module_code"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("role_modules")
    op.drop_index(op.f("ix_user_roles_user_id"), table_name="user_roles")
    op.drop_index(op.f("ix_user_roles_role_id"), table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("modules")
    op.drop_index(op.f("ix_roles_tenant_id"), table_name="roles")
    op.drop_table("roles")
