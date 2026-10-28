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


def _m003_account_icon(conn: Connection) -> None:
    """account.icon: chosen bank icon ('' = automatic)."""
    if "icon" not in _columns(conn, "account"):
        conn.exec_driver_sql("ALTER TABLE account ADD COLUMN icon VARCHAR NOT NULL DEFAULT ''")


def _m004_kind_manual(conn: Connection) -> None:
    """transaction.kind_manual: the type was set by hand (rules must not override it)."""
    if "kind_manual" not in _columns(conn, "transaction"):
        conn.exec_driver_sql('ALTER TABLE "transaction" ADD COLUMN kind_manual BOOLEAN NOT NULL DEFAULT 0')


def _m005_label_interval(conn: Connection) -> None:
    """label.frequency (monthly/quarterly/yearly) -> label.every + label.unit,
    so any interval can be set (weekly, half-yearly, every 2 months...)."""
    cols = _columns(conn, "label")
    if "every" not in cols:
        conn.exec_driver_sql("ALTER TABLE label ADD COLUMN every INTEGER")
    if "unit" not in cols:
        conn.exec_driver_sql("ALTER TABLE label ADD COLUMN unit VARCHAR(5)")
    if "frequency" in cols:
        for freq, every, unit in (("monthly", 1, "month"), ("quarterly", 3, "month"), ("yearly", 1, "year")):
            conn.exec_driver_sql("UPDATE label SET every = ?, unit = ? WHERE frequency = ?", (every, unit, freq))
        conn.exec_driver_sql("ALTER TABLE label DROP COLUMN frequency")


MIGRATIONS = [
    _m001_mask_account_number,
    _m002_database_id,
    _m003_account_icon,
    _m004_kind_manual,
    _m005_label_interval,
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
