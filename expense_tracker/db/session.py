"""Engine, startup backup and single-writer lock.

The DB may sit in a Google Drive for Desktop folder. To keep that safe:
  - journal_mode=DELETE (not WAL): Drive syncs a single self-contained file
    instead of a .db + -wal pair that can arrive out of step.
  - a lock file records which machine has the DB open, so opening it on a
    second PC at the same time is flagged instead of silently forking data.
  - a timestamped backup is taken on every launch.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from ..config import Settings, settings
from .migrations import run_migrations

_engine = None
LOCK_STALE_AFTER = timedelta(hours=12)


def _make_engine(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def backup_db(cfg: Settings) -> Path | None:
    if not cfg.db_path.exists():
        return None
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.backup_dir / f"expense_tracker_{datetime.now():%Y%m%d_%H%M%S}.db"
    shutil.copy2(cfg.db_path, dest)
    backups = sorted(cfg.backup_dir.glob("expense_tracker_*.db"))
    for old in backups[: -cfg.backups_to_keep]:
        old.unlink()
    return dest


def _lock_path(cfg: Settings) -> Path:
    return cfg.data_path / "expense_tracker.lock"


def acquire_lock(cfg: Settings) -> str | None:
    """Take the lock. Returns a warning message if another machine appears to
    hold it (the lock is still taken: the user decides what to do)."""
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    path = _lock_path(cfg)
    me = socket.gethostname()
    warning = None
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            since = datetime.fromisoformat(info["since"])
            if info.get("host") != me and datetime.now() - since < LOCK_STALE_AFTER:
                warning = (
                    f"Database looks open on '{info.get('host')}' since {since:%d %b %H:%M}. "
                    "Close it there first to avoid sync conflicts."
                )
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    path.write_text(json.dumps({"host": me, "pid": os.getpid(), "since": datetime.now().isoformat()}),
                    encoding="utf-8")
    return warning


def release_lock(cfg: Settings) -> None:
    path = _lock_path(cfg)
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if info.get("host") == socket.gethostname() and info.get("pid") == os.getpid():
            path.unlink()
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def init_db(cfg: Settings = settings, db_path: Path | None = None):
    """Create the engine and tables. Pass db_path (e.g. ':memory:' tests) to skip
    the data-dir handling."""
    global _engine
    from . import models  # noqa: F401  (register tables)

    if db_path is None:
        cfg.data_path.mkdir(parents=True, exist_ok=True)
        db_path = cfg.db_path
    _engine = _make_engine(db_path)
    SQLModel.metadata.create_all(_engine)
    run_migrations(_engine)
    return _engine


def database_id() -> str:
    """Permanent unique id of the open database (created by migration 002)."""
    from .models import Meta

    with get_session() as s:
        return s.get(Meta, "db_id").value


@contextmanager
def get_session():
    if _engine is None:
        init_db()
    with Session(_engine, expire_on_commit=False) as session:
        yield session
