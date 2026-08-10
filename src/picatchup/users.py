"""Who is allowed to receive anything.

No row here means no access at all. That is the entire access-control system,
and at our size it is the right one.
"""

from __future__ import annotations

import sqlite3

from picatchup.util import new_id, utc_now


def add(
    conn: sqlite3.Connection, telegram_user_id: int, display_name: str, *, is_admin: bool = False
) -> None:
    """Add someone, or bring back someone who was removed earlier."""
    with conn:
        conn.execute(
            "INSERT INTO users (id, telegram_user_id, display_name, is_admin, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (telegram_user_id) DO UPDATE SET display_name = excluded.display_name,"
            " is_admin = excluded.is_admin, is_active = 1",
            (new_id(), telegram_user_id, display_name, int(is_admin), utc_now()),
        )


def remove(conn: sqlite3.Connection, telegram_user_id: int) -> bool:
    """Delete someone, and with them the record of what they were sent.

    Deleting rather than deactivating, because "I want to be removed" means
    removed. Returns False if there was nobody with that id.
    """
    with conn:
        cursor = conn.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
    return cursor.rowcount > 0


def listing(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT telegram_user_id, display_name, is_admin, is_active, is_muted FROM users"
        " ORDER BY display_name"
    ).fetchall()


def recipients(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Everyone who should get today's recap."""
    return conn.execute(
        "SELECT id, telegram_user_id, display_name FROM users"
        " WHERE is_active = 1 AND is_muted = 0 ORDER BY display_name"
    ).fetchall()
