"""отпечаток дедупа

Revision ID: 4f31790b279b
Revises: c3f81d6a94b7
Create Date: 2026-08-31 23:08:55.986050

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.models.types  # noqa: F401  — денежный тип в автогенерации

# revision identifiers, used by Alembic.
revision: str = '4f31790b279b'
down_revision: str | Sequence[str] | None = 'c3f81d6a94b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("transactions", sa.Column("dedup_key", sa.String(length=64), nullable=True))
    op.add_column("transactions", sa.Column("dedup_seq", sa.Integer(), nullable=True))
    op.create_index("ix_transactions_dedup_key", "transactions", ["dedup_key"])

    # те же поля и тот же разделитель, что в ParsedRow.dedup_key;
    # сумма приводится к тексту без хвостовых нулей, как format(Decimal, "f")
    op.execute(
        """
        update transactions set dedup_key = encode(sha256(convert_to(concat_ws(
            chr(31),
            to_char(date, 'YYYY-MM-DD'),
            category_name,
            account_name,
            coalesce(payee, ''),
            coalesce(comment, ''),
            direction::text,
            case when position('.' in amount_original::text) > 0
                 then trim(trailing '.' from trim(trailing '0' from amount_original::text))
                 else amount_original::text end,
            currency
        ), 'UTF8')), 'hex')
        where source::text = 'csv'
        """
    )
    op.execute(
        """
        update transactions t set dedup_seq = s.seq from (
            select id, row_number() over (
                partition by user_id, dedup_key order by id
            ) - 1 as seq
            from transactions where dedup_key is not null
        ) s where t.id = s.id
        """
    )

    op.drop_constraint("uq_tx_user_zen_created", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_tx_user_dedup", "transactions", ["user_id", "dedup_key", "dedup_seq"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_tx_user_dedup", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_tx_user_zen_created", "transactions", ["user_id", "zen_created_at"]
    )
    op.drop_index("ix_transactions_dedup_key", table_name="transactions")
    op.drop_column("transactions", "dedup_seq")
    op.drop_column("transactions", "dedup_key")
