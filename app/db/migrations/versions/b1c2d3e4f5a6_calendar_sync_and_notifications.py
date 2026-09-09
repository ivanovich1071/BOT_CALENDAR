"""sync_token у календаря, календарь по умолчанию у сотрудника, уведомления

Revision ID: b1c2d3e4f5a6
Revises: ad333ed7e730
Create Date: 2026-09-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b1c2d3e4f5a6'
down_revision: str | None = 'ad333ed7e730'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # syncToken принадлежит календарю, а не аккаунту: токен одного календаря
    # непригоден для другого — Google отвечает 410 и синхронизация вырождается
    # в полный перебор при каждом запуске.
    op.add_column('calendars', sa.Column('sync_token', sa.Text(), nullable=True))

    # Явно выбранный рабочий календарь сотрудника (пусто — primary аккаунта).
    op.add_column('employees', sa.Column('default_calendar_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_employees_default_calendar',
        'employees',
        'calendars',
        ['default_calendar_id'],
        ['id'],
        ondelete='SET NULL',
    )

    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id', 'kind', name='uq_notification_booking_kind'),
    )
    op.create_index(op.f('ix_notifications_booking_id'), 'notifications', ['booking_id'])

    # Старое поле на аккаунте больше не используется — переносим то, что накопилось,
    # в primary-календарь аккаунта, чтобы не терять точку синхронизации.
    op.execute(
        """
        UPDATE calendars c
           SET sync_token = ga.sync_token
        FROM google_accounts ga
        WHERE c.google_account_id = ga.id
          AND c.is_primary
          AND ga.sync_token IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_notifications_booking_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_constraint('fk_employees_default_calendar', 'employees', type_='foreignkey')
    op.drop_column('employees', 'default_calendar_id')
    op.drop_column('calendars', 'sync_token')
