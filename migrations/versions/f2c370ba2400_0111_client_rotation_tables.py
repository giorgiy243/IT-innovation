"""0111 - таблицы модуля «Ротация клиентов»

Revision ID: f2c370ba2400
Revises: f1a8d3c5e7b2
Create Date: 2026-06-30

Три таблицы модуля client_rotation (план 2026-06-30, Фаза 1.1):
- client_rotation_data - расширенные данные клиента (скоринг, ДСП/СП, контакты);
- assignments - решение по ротации (принимающий менеджер + ручной статус);
- summaries - LLM-саммари + проверенный контакт.
Все ссылаются на companies.id (CASCADE) и tenants.id (RESTRICT); tenant_id
проиндексирован. JSON-поля - JSONB на PostgreSQL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2c370ba2400"
down_revision = "f1a8d3c5e7b2"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "client_rotation_data",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("current_manager", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("is_orphan", sa.Boolean(), nullable=False),
        sa.Column("in_sp", sa.Boolean(), nullable=False),
        sa.Column("in_dsp", sa.Boolean(), nullable=False),
        sa.Column("days_no_contact", sa.Integer(), nullable=True),
        sa.Column("days_no_kp", sa.Integer(), nullable=True),
        sa.Column("days_no_shipment", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=100), nullable=True),
        sa.Column("contact_person", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("site", sa.String(length=500), nullable=True),
        sa.Column("employees", sa.Integer(), nullable=True),
        sa.Column("activity", sa.Text(), nullable=True),
        sa.Column("turnover_json", _JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("dsp_info", sa.Text(), nullable=True),
        sa.Column("sp_info", sa.Text(), nullable=True),
        sa.Column("comments_corpus", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_breakdown_json", _JSONB, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("transfer_status", sa.String(length=100), nullable=True),
        sa.Column("holding_members_json", _JSONB, nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_crd_company"),
    )
    op.create_index(op.f("ix_client_rotation_data_tenant_id"), "client_rotation_data", ["tenant_id"], unique=False)
    op.create_index("ix_crd_score", "client_rotation_data", ["score"], unique=False)

    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to_employee_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("transfer_status", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_assignments_company"),
    )
    op.create_index(op.f("ix_assignments_assigned_to_employee_id"), "assignments", ["assigned_to_employee_id"], unique=False)
    op.create_index(op.f("ix_assignments_tenant_id"), "assignments", ["tenant_id"], unique=False)

    op.create_table(
        "summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("size_tier", sa.Integer(), nullable=True),
        sa.Column("workstations", sa.Integer(), nullable=True),
        sa.Column("contact_name", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_summaries_company"),
    )
    op.create_index(op.f("ix_summaries_tenant_id"), "summaries", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_summaries_tenant_id"), table_name="summaries")
    op.drop_table("summaries")
    op.drop_index(op.f("ix_assignments_tenant_id"), table_name="assignments")
    op.drop_index(op.f("ix_assignments_assigned_to_employee_id"), table_name="assignments")
    op.drop_table("assignments")
    op.drop_index("ix_crd_score", table_name="client_rotation_data")
    op.drop_index(op.f("ix_client_rotation_data_tenant_id"), table_name="client_rotation_data")
    op.drop_table("client_rotation_data")
