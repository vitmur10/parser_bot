import sqlite3
from typing import Optional

from db import DB_PATH


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def delete_subscription(sub_id: int, user_id: int) -> bool:
    """
    Видаляє підписку фізично з БД.
    Повертає True, якщо щось реально видалили.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
            (sub_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_all_for_user(user_id: int) -> int:
    """
    Видаляє всі підписки юзера фізично.
    Повертає кількість видалених рядків.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
