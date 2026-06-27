"""0106 - вендоры: vendors, vendor_distributors

Revision ID: b9d4e2f7a3c1
Revises: c3e7f01a2d88
Create Date: 2026-06-27

vendors.portal_password_enc хранит Fernet-токен (зашифрованный blob).
Расшифровка только через core/vendors/crypto.py с ключом VENDOR_CRYPTO_KEY.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b9d4e2f7a3c1"
down_revision = "c3e7f01a2d88"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("categories", sa.String(500), nullable=True),
        sa.Column("status_type", sa.String(20), nullable=True),
        sa.Column("status_text", sa.String(500), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("partner_id", sa.String(100), nullable=True),
        sa.Column("legal_entity", sa.String(255), nullable=True),
        sa.Column("directions", sa.String(500), nullable=True),
        sa.Column("discount", sa.String(100), nullable=True),
        sa.Column("purchase_method", sa.String(100), nullable=True),
        sa.Column("portal_url", sa.String(500), nullable=True),
        sa.Column("portal_login", sa.String(255), nullable=True),
        sa.Column("portal_password_enc", sa.Text(), nullable=True),
        sa.Column("vendor_contact", sa.String(500), nullable=True),
        sa.Column("deal_registration", sa.String(500), nullable=True),
        sa.Column("mop_comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_vendors_tenant_name"),
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])
    op.create_index("ix_vendors_status_type", "vendors", ["status_type"])

    op.create_table(
        "vendor_distributors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("contact", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vendor_distributors_vendor_id", "vendor_distributors", ["vendor_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_distributors_vendor_id", table_name="vendor_distributors")
    op.drop_table("vendor_distributors")

    op.drop_index("ix_vendors_status_type", table_name="vendors")
    op.drop_index("ix_vendors_tenant_id", table_name="vendors")
    op.drop_table("vendors")
