"""0110 - companies.source_key (стабильный ключ клиента), inn -> nullable

Revision ID: f1a8d3c5e7b2
Revises: c5e8a2f4d6b3
Create Date: 2026-06-30

Подготовка под модуль «Ротация клиентов» (план 2026-06-30, Фаза 1.0).
В выгрузках ИНН есть не у всех клиентов (~15% без него), но клиент реальный.
Вводим source_key - стабильный ключ из источника (ИНН либо суррогат), уникальный
в рамках tenant. inn становится nullable. На companies нет внешних FK -
изменение ключа безопасно.

Порядок upgrade исключает нарушение NOT NULL на непустой таблице:
add nullable -> backfill (source_key=inn) -> set NOT NULL -> inn nullable ->
переключить UNIQUE с inn на source_key.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a8d3c5e7b2"
down_revision = "c5e8a2f4d6b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Новый ключ добавляем временно nullable, чтобы не упасть на данных.
    op.add_column("companies", sa.Column("source_key", sa.String(length=150), nullable=True))
    # 2. Бэкфилл: у существующих строк ключ = текущий ИНН (он был NOT NULL).
    op.execute("UPDATE companies SET source_key = inn WHERE source_key IS NULL")
    # 3. Теперь ключ обязателен.
    op.alter_column("companies", "source_key", existing_type=sa.String(length=150), nullable=False)
    # 4. ИНН больше не обязателен (клиенты без ИНН - реальные).
    op.alter_column("companies", "inn", existing_type=sa.String(length=12), nullable=True)
    # 5. Уникальность переключаем с inn на source_key.
    op.drop_constraint("uq_companies_tenant_inn", "companies", type_="unique")
    op.create_unique_constraint(
        "uq_companies_tenant_source_key", "companies", ["tenant_id", "source_key"]
    )


def downgrade() -> None:
    # Обратный порядок. ВНИМАНИЕ: откат требует, чтобы не было строк с inn IS NULL
    # (иначе NOT NULL не выставится) и чтобы inn был уникален в рамках tenant.
    op.drop_constraint("uq_companies_tenant_source_key", "companies", type_="unique")
    op.create_unique_constraint("uq_companies_tenant_inn", "companies", ["tenant_id", "inn"])
    op.alter_column("companies", "inn", existing_type=sa.String(length=12), nullable=False)
    op.drop_column("companies", "source_key")
