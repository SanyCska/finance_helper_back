"""Средства, подписки, категории и валюты в плане

Revision ID: 9a1c4f0b7d21
Revises: 0546109b417a
Create Date: 2026-08-03 01:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.models.types

# revision identifiers, used by Alembic.
revision: str = '9a1c4f0b7d21'
down_revision: str | Sequence[str] | None = '0546109b417a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Новое значение `recurring` в `TxSource` схему не меняет: колонка `source` —
# обычный varchar, CHECK-ограничения у неё нет (`create_constraint` по умолчанию
# выключен в SQLAlchemy 2.0).


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'fund_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('currency', sa.String(length=16), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_fund_sources_user_id'), 'fund_sources', ['user_id'], unique=False)

    op.create_table(
        'fund_balances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('amount_original', app.models.types.Money(precision=18, scale=4), nullable=False),
        sa.Column('currency', sa.String(length=16), nullable=False),
        sa.Column('amount_base', app.models.types.Money(precision=18, scale=4), nullable=True),
        sa.Column('fx_rate', app.models.types.Money(precision=24, scale=10), nullable=True),
        sa.Column(
            'fx_status',
            sa.Enum('ok', 'approx', 'pending', name='fxstatus', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['fund_sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_fund_balances_user_id'), 'fund_balances', ['user_id'], unique=False)
    op.create_index(
        op.f('ix_fund_balances_source_id'), 'fund_balances', ['source_id'], unique=False
    )
    op.create_index('ix_balance_source_date', 'fund_balances', ['source_id', 'date'], unique=False)

    op.create_table(
        'month_checks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('real_saldo', app.models.types.Money(precision=14, scale=2), nullable=False),
        sa.Column('tracked_saldo', app.models.types.Money(precision=14, scale=2), nullable=False),
        sa.Column('discrepancy', app.models.types.Money(precision=14, scale=2), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'month', name='uq_check_user_month'),
    )
    op.create_index(op.f('ix_month_checks_user_id'), 'month_checks', ['user_id'], unique=False)

    op.create_table(
        'recurring_expenses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'subscription', 'rent', name='recurringkind', native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('amount', app.models.types.Money(precision=14, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=16), nullable=False),
        sa.Column('period_months', sa.Integer(), nullable=False),
        sa.Column('charge_day', sa.Integer(), nullable=False),
        sa.Column('category_name', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_recurring_expenses_user_id'), 'recurring_expenses', ['user_id'], unique=False
    )

    op.add_column('plan_lines', sa.Column('currency', sa.String(length=16), nullable=True))
    op.add_column('plan_lines', sa.Column('category_names', sa.JSON(), nullable=True))
    op.execute("UPDATE plan_lines SET currency = 'USD' WHERE currency IS NULL")
    op.execute("UPDATE plan_lines SET category_names = '[]' WHERE category_names IS NULL")
    op.alter_column('plan_lines', 'currency', nullable=False)
    op.alter_column('plan_lines', 'category_names', nullable=False)

    op.add_column('transactions', sa.Column('recurring_id', sa.Integer(), nullable=True))
    op.add_column('transactions', sa.Column('recurring_month', sa.Date(), nullable=True))
    op.create_foreign_key(
        'fk_tx_recurring',
        'transactions',
        'recurring_expenses',
        ['recurring_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_unique_constraint(
        'uq_tx_recurring_month', 'transactions', ['recurring_id', 'recurring_month']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM transactions WHERE source = 'recurring'")
    op.drop_constraint('uq_tx_recurring_month', 'transactions', type_='unique')
    op.drop_constraint('fk_tx_recurring', 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'recurring_month')
    op.drop_column('transactions', 'recurring_id')

    op.drop_column('plan_lines', 'category_names')
    op.drop_column('plan_lines', 'currency')

    op.drop_index(op.f('ix_recurring_expenses_user_id'), table_name='recurring_expenses')
    op.drop_table('recurring_expenses')
    op.drop_index(op.f('ix_month_checks_user_id'), table_name='month_checks')
    op.drop_table('month_checks')
    op.drop_index('ix_balance_source_date', table_name='fund_balances')
    op.drop_index(op.f('ix_fund_balances_source_id'), table_name='fund_balances')
    op.drop_index(op.f('ix_fund_balances_user_id'), table_name='fund_balances')
    op.drop_table('fund_balances')
    op.drop_index(op.f('ix_fund_sources_user_id'), table_name='fund_sources')
    op.drop_table('fund_sources')
