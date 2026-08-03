"""Конкретная дата списания у подписки

Номера дня мало: у годовой подписки без месяца непонятно, когда именно снимают
деньги. `charge_day` заменён датой `charge_on`; при переносе день берётся
из старого поля, а месяц — из месяца, с которого подписка начисляется.

Revision ID: b7e2c05a3f18
Revises: 9a1c4f0b7d21
Create Date: 2026-08-03 16:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e2c05a3f18'
down_revision: str | Sequence[str] | None = '9a1c4f0b7d21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `starts_on` всегда первое число месяца, поэтому дата собирается сдвигом
#: на `charge_day - 1` дней; день подрезается по длине месяца, чтобы 31-е
#: в феврале не уехало на март
_FILL = """
UPDATE recurring_expenses
SET charge_on = starts_on + (
    LEAST(
        charge_day,
        EXTRACT(DAY FROM (starts_on + INTERVAL '1 month - 1 day'))::int
    ) - 1
) * INTERVAL '1 day'
WHERE charge_on IS NULL
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('recurring_expenses', sa.Column('charge_on', sa.Date(), nullable=True))
    op.execute(_FILL)
    op.alter_column('recurring_expenses', 'charge_on', nullable=False)
    op.drop_column('recurring_expenses', 'charge_day')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('recurring_expenses', sa.Column('charge_day', sa.Integer(), nullable=True))
    op.execute("UPDATE recurring_expenses SET charge_day = EXTRACT(DAY FROM charge_on)::int")
    op.alter_column('recurring_expenses', 'charge_day', nullable=False)
    op.drop_column('recurring_expenses', 'charge_on')
