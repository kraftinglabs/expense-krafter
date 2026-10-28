import sqlite3

from expense_tracker.db import get_session, init_db
from expense_tracker.db.migrations import MIGRATIONS
from expense_tracker.db.models import Account, AccountType, IntervalUnit, Label
from sqlmodel import select


def test_old_account_schema_is_upgraded(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE account (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, type VARCHAR(11) NOT NULL,
                   institution VARCHAR NOT NULL, last4 VARCHAR NOT NULL, parser_key VARCHAR NOT NULL,
                   statement_day INTEGER, due_day INTEGER, active BOOLEAN NOT NULL)""")
    con.execute("INSERT INTO account VALUES (1, 'SBI Savings', 'bank', 'SBI', '1234', 'generic_excel', NULL, NULL, 1)")
    con.commit()
    con.close()

    init_db(db_path=db)
    with get_session() as s:
        s.add(Account(name="New card", type=AccountType.credit_card, number_masked="43210XXXXXXXXXXX"))
        s.commit()
        accounts = s.exec(select(Account).order_by(Account.id)).all()
    assert [a.name for a in accounts] == ["SBI Savings", "New card"]
    assert accounts[0].number_masked == ""

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(account)")}
    assert "last4" not in cols and "number_masked" in cols
    assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_fresh_db_is_marked_current(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db_path=db)
    init_db(db_path=db)  # second start is a no-op
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_label_frequency_becomes_interval(tmp_path):
    db = tmp_path / "labels.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE label (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, color VARCHAR NOT NULL,
                   is_recurring BOOLEAN NOT NULL, frequency VARCHAR(9))""")
    con.executemany("INSERT INTO label VALUES (?, ?, '#000000', ?, ?)",
                    [(1, "Subscriptions", 1, "monthly"), (2, "Insurance", 1, "yearly"),
                     (3, "Gym", 1, "quarterly"), (4, "Food", 0, None)])
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()

    init_db(db_path=db)
    with get_session() as s:
        got = {lab.name: (lab.every, lab.unit) for lab in s.exec(select(Label))}
    assert got == {"Subscriptions": (1, IntervalUnit.month), "Insurance": (1, IntervalUnit.year),
                   "Gym": (3, IntervalUnit.month), "Food": (None, None)}
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(label)")}
    assert "frequency" not in cols
