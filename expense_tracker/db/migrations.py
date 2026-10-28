"""Schema migrations for existing databases.

New tables are created by SQLModel's create_all; changes to existing tables go
here. Each migration runs once, tracked with SQLite's PRAGMA user_version.
Append new migrations to the list; never edit or reorder old ones. Migrations
must be safe to run on a freshly created (already up-to-date) schema too.

A backup of the DB is taken on every launch before this runs.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Connection, Engine


def _columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")')}


def _m001_mask_account_number(conn: Connection) -> None:
    """account.last4 -> account.number_masked (first 5 chars only)."""
    cols = _columns(conn, "account")
    if "number_masked" not in cols:
        conn.exec_driver_sql("ALTER TABLE account ADD COLUMN number_masked VARCHAR NOT NULL DEFAULT ''")
    if "last4" in cols:
        conn.exec_driver_sql("ALTER TABLE account DROP COLUMN last4")


def _m002_database_id(conn: Connection) -> None:
    """Give the database a permanent unique id. Things stored outside the DB
    (saved statement passwords) are keyed by it, so they never leak to another
    database or survive a reset."""
    conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)")
    conn.exec_driver_sql("INSERT OR IGNORE INTO meta (key, value) VALUES ('db_id', ?)", (uuid.uuid4().hex,))


MIGRATIONS = [
    _m001_mask_account_number,
    _m002_database_id,
]


def run_migrations(engine: Engine) -> int:
    """Apply pending migrations. Returns how many ran."""
    with engine.begin() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar() or 0
        pending = MIGRATIONS[version:]
        for migration in pending:
            migration(conn)
        if pending:
            conn.exec_driver_sql(f"PRAGMA user_version = {len(MIGRATIONS)}")
    return len(pending)
