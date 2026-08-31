"""Удаление дублей CSV-операций, оставшихся от дедупа по createdDate.

Дзен отдаёт `createdDate` в таймзоне устройства на момент выгрузки, и
выгрузка из другого пояса заезжала как новая история целиком. Скрипт
оставляет по одной операции на каждый набор одинаковых полей, выбирая
самую раннюю по id.

По умолчанию только считает. Удаляет с флагом --apply.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.db import SessionLocal

#: строки-дубли: всё, кроме первой в каждой группе одинаковых операций
EXTRA_ROWS = """
    select id from (
        select id, row_number() over (
            partition by user_id, date, category_name, account_name,
                         coalesce(payee, ''), coalesce(comment, ''),
                         direction, amount_original, currency
            order by id
        ) as rn
        from transactions where source::text = 'csv'
    ) t where rn > 1
"""

BREAKDOWN = text(
    """
    select user_id, source::text as source, count(*) as n
    from transactions group by user_id, source::text order by user_id, source::text
    """
)


def main() -> int:
    apply = "--apply" in sys.argv
    with SessionLocal() as db:
        total = db.execute(text("select count(*) from transactions")).scalar_one()
        extra = db.execute(text(f"select count(*) from ({EXTRA_ROWS}) x")).scalar_one()

        print(f"всего операций {total}, лишних {extra}, останется {total - extra}")
        print("сейчас в базе:")
        for user_id, source, count in db.execute(BREAKDOWN):
            print(f"  user={user_id} source={source} строк={count}")

        if not apply:
            print("сухой прогон, ничего не удалено; для удаления добавь --apply")
            return 0

        deleted = db.execute(
            text(f"delete from transactions where id in ({EXTRA_ROWS})")
        ).rowcount
        db.commit()
        print(f"удалено {deleted}")
        print("после удаления:")
        for user_id, source, count in db.execute(BREAKDOWN):
            print(f"  user={user_id} source={source} строк={count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
