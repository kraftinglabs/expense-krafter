import sqlite3

import pytest

from expensekrafter.db import init_db
from expensekrafter.services import app_lock


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "lock.db"
    init_db(db_path=path)
    return path


def test_set_change_and_remove(db):
    assert not app_lock.is_set() and not app_lock.is_set_in(db)
    app_lock.set_password("open sesame", "open sesame")
    assert app_lock.is_set() and app_lock.is_set_in(db)
    assert app_lock.verify("open sesame") and not app_lock.verify("open sesam")

    with pytest.raises(ValueError, match="current password"):
        app_lock.set_password("new secret", "new secret", current="wrong")
    app_lock.set_password("new secret", "new secret", current="open sesame")
    assert app_lock.verify("new secret") and not app_lock.verify("open sesame")

    with pytest.raises(ValueError, match="current password"):
        app_lock.remove_password("open sesame")
    app_lock.remove_password("new secret")
    assert not app_lock.is_set()


def test_new_password_rules(db):
    with pytest.raises(ValueError, match="at least"):
        app_lock.set_password("short", "short")
    with pytest.raises(ValueError, match="match"):
        app_lock.set_password("long enough", "long enougj")
    assert not app_lock.is_set()


def test_only_a_salted_hash_is_stored(db):
    app_lock.set_password("open sesame", "open sesame")
    stored = sqlite3.connect(db).execute("SELECT value FROM meta WHERE key = ?", (app_lock.KEY,)).fetchone()[0]
    assert "open sesame" not in stored and stored.startswith("scrypt$")
    first = stored
    app_lock.reset()  # the --reset-password path
    assert not app_lock.is_set()
    app_lock.set_password("open sesame", "open sesame")
    second = sqlite3.connect(db).execute("SELECT value FROM meta WHERE key = ?", (app_lock.KEY,)).fetchone()[0]
    assert first != second  # new salt each time


def test_each_run_needs_a_new_sign_in():
    assert app_lock.session_token() != app_lock.session_token()


def test_is_set_in_accepts_a_relative_path(db, monkeypatch):
    app_lock.set_password("open sesame", "open sesame")
    monkeypatch.chdir(db.parent)
    assert app_lock.is_set_in(type(db)(db.name))  # e.g. --data-dir .devdata
