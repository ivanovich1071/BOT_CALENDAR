"""Демо-доступ: пометка демо-логинов, Демо-специалиста, записей и клиентов гостя

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-09-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'd1e2f3a4b5c6'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('users', 'employees', 'bookings', 'clients')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('is_demo', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, 'is_demo')
