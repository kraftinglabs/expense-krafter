"""The app password: set in Settings, asked for every time the app starts.

Only a salted scrypt hash is stored, in the database's meta table, so the
password travels with the data (e.g. to another PC through Google Drive).
It locks the app's screens; it does not encrypt the database file. Anyone who
can open that file directly can read it, and can also remove the password
with `expensekrafter --reset-password`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path

from ..db import get_session
from ..db.models import Meta

KEY = "app_password"
MIN_LENGTH = 6
# scrypt cost: ~50 ms per check, which makes guessing slow without making
# signing in feel slow.
_N, _R, _P = 2**14, 8, 1


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def _encode(password: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${salt.hex()}${_hash(password, salt).hex()}"


def _stored() -> str | None:
    with get_session() as s:
        row = s.get(Meta, KEY)
        return row.value if row else None


def is_set() -> bool:
    return _stored() is not None


def is_set_in(db_path: Path) -> bool:
    """is_set() for a database that isn't open yet, read-only (used before
    startup, without running migrations or taking the lock)."""
    if not db_path.exists():
        return False
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT 1 FROM meta WHERE key = ?", (KEY,)).fetchone() is not None
    except sqlite3.OperationalError:  # no meta table yet
        return False
    finally:
        con.close()


def verify(password: str) -> bool:
    stored = _stored()
    if not stored:
        return False
    _, salt, digest = stored.split("$")
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt)).hex(), digest)


def _check_new(new: str, confirm: str) -> None:
    if len(new) < MIN_LENGTH:
        raise ValueError(f"Use at least {MIN_LENGTH} characters")
    if new != confirm:
        raise ValueError("The two passwords don't match")


def set_password(new: str, confirm: str, current: str | None = None) -> None:
    """Set the password, or change it (then `current` must be right)."""
    if is_set() and not verify(current or ""):
        raise ValueError("The current password is wrong")
    _check_new(new, confirm)
    with get_session() as s:
        s.merge(Meta(key=KEY, value=_encode(new)))
        s.commit()


def remove_password(current: str) -> None:
    if not verify(current):
        raise ValueError("The current password is wrong")
    reset()


def reset() -> None:
    """Remove the password without knowing it (the --reset-password option)."""
    with get_session() as s:
        if row := s.get(Meta, KEY):
            s.delete(row)
            s.commit()


def session_token() -> str:
    """A fresh value per app start: a sign-in only counts for the run it
    happened in, so the app asks again every time it's opened."""
    return secrets.token_urlsafe(16)
