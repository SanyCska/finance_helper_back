"""Починка колонки категорий в строках плана

Ревизия 9a1c4f0b7d21 сначала добавляла одну категорию (`category_name`), а
позже была переписана на список (`category_names`). Базы, накатившие её до
правки, остались со старой колонкой: alembic считает ревизию применённой и
второй раз её не выполнит, а код уже читает `category_names` — отсюда 500
на экранах плана.

Эта ревизия приводит схему к нужному виду и молчит, если всё уже на месте.

Revision ID: c3f81d6a94b7
Revises: b7e2c05a3f18
Create Date: 2026-08-03 17:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f81d6a94b7'
down_revision: str | Sequence[str] | None = 'b7e2c05a3f18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = 'plan_lines'


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column['name'] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    """Upgrade schema."""
    columns = _columns()

    if 'category_names' not in columns:
        op.add_column(TABLE, sa.Column('category_names', sa.JSON(), nullable=True))
        if 'category_name' in columns:
            # одна категория превращается в список из неё же, пустая — в пустой список
            op.execute(
                f"UPDATE {TABLE} SET category_names = CASE "  # noqa: S608
                "WHEN category_name IS NULL OR category_name = '' THEN '[]' "
                "ELSE json_build_array(category_name)::text::json END"
            )
        op.execute(f"UPDATE {TABLE} SET category_names = '[]' WHERE category_names IS NULL")  # noqa: S608
        op.alter_column(TABLE, 'category_names', nullable=False)

    if 'category_name' in columns:
        op.drop_column(TABLE, 'category_name')


def downgrade() -> None:
    """Downgrade schema.

    Обратно раскладываем в одну категорию: берём первую из списка, остальные
    в старую схему не помещаются.
    """
    columns = _columns()
    if 'category_name' not in columns:
        op.add_column(TABLE, sa.Column('category_name', sa.Text(), nullable=True))
        op.execute(
            f"UPDATE {TABLE} SET category_name = category_names->>0 "  # noqa: S608
            "WHERE json_array_length(category_names) > 0"
        )
