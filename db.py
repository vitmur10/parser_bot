# db.py
import sqlite3
from typing import List, Dict, Any, Tuple
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.sqlite3"


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Ініціалізація БД:
    - створює таблицю subscriptions, якщо її ще немає
    - додає колонку sizes при оновленні схеми
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # Базова схема (включаючи поле sizes)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                brand TEXT,
                sizes TEXT,
                last_status TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, url)
            )
            """
        )
        conn.commit()

        # Міграція на випадок, якщо таблиця вже була створена раніше БЕЗ поля sizes
        try:
            cur.execute("ALTER TABLE subscriptions ADD COLUMN sizes TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # Колонка вже існує — ігноруємо
            pass

    finally:
        conn.close()

def add_subscription(
    user_id: int,
    chat_id: int,
    url: str,
    brand: str | None,
    last_status: str | None,
    sizes: str | None = None,
):
    """
    Додає підписку.
    UNIQUE(user_id, url) — один юзер не може додати той самий URL двічі.
    sizes:
      - None або ""  → слідкуємо за всіма розмірами
      - "M,L,XL"     → слідкуємо тільки за цими розмірами
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO subscriptions (user_id, chat_id, url, brand, sizes, last_status, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (user_id, chat_id, url, brand, sizes, last_status),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_subscriptions() -> List[sqlite3.Row]:
    """
    Повертає всі активні підписки.
    Використовується monitor_loop.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, chat_id, url, brand, sizes, last_status
            FROM subscriptions
            WHERE is_active = 1
            """
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def update_subscription_status(sub_id: int, new_status: str):
    """
    Оновлює last_status для конкретної підписки.
    Використовується monitor_loop після перевірки.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE subscriptions
            SET last_status = ?
            WHERE id = ?
            """,
            (new_status, sub_id),
        )
        conn.commit()
    finally:
        conn.close()

def get_user_subscriptions(user_id: int) -> List[sqlite3.Row]:
    """
    Повертає всі підписки конкретного користувача (активні/неактивні),
    для команди типу /my_links.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, url, brand, sizes, last_status, is_active, created_at
            FROM subscriptions
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()
