"""Компания и база знаний: исключения расписания, услуги сотрудников, архив,
история диалогов с ИИ, очередь уведомлений

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-09-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'b1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('bio', sa.Text(), nullable=True))
    op.add_column('employees', sa.Column('telegram_user_id', sa.BigInteger(), nullable=True))
    op.create_unique_constraint('uq_employees_telegram_user_id', 'employees', ['telegram_user_id'])
    op.add_column('employees', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('services', sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False))
    op.add_column('services', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('clients', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'employee_services',
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('employee_id', 'service_id'),
    )

    op.create_table(
        'schedule_exceptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('date_from', sa.Date(), nullable=False),
        sa.Column('date_to', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=True),
        sa.Column('end_time', sa.Time(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_schedule_exceptions_employee_id'), 'schedule_exceptions', ['employee_id'])

    op.create_table(
        'knowledge_articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tool_name', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_messages_client_created', 'ai_messages', ['client_id', 'created_at'])

    op.create_table(
        'outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('outbox')
    op.drop_index('ix_ai_messages_client_created', table_name='ai_messages')
    op.drop_table('ai_messages')
    op.drop_table('knowledge_articles')
    op.drop_index(op.f('ix_schedule_exceptions_employee_id'), table_name='schedule_exceptions')
    op.drop_table('schedule_exceptions')
    op.drop_table('employee_services')
    op.drop_column('clients', 'archived_at')
    op.drop_column('services', 'archived_at')
    op.drop_column('services', 'sort_order')
    op.drop_column('employees', 'archived_at')
    op.drop_constraint('uq_employees_telegram_user_id', 'employees', type_='unique')
    op.drop_column('employees', 'telegram_user_id')
    op.drop_column('employees', 'bio')
