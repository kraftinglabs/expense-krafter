import sqlite3

from expensekrafter.db import get_session, init_db
from expensekrafter.db.migrations import MIGRATIONS
from expensekrafter.db.models import Account, AccountType, IntervalUnit, Label
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


def test_split_shares_move_to_the_new_table(tmp_path):
    from datetime import date
    from expensekrafter.db.models import Direction, Transaction

    db = tmp_path / "splits.db"
    init_db(db_path=db)
    with get_session() as s:
        acc = Account(name="Bank", type=AccountType.bank)
        s.add(acc)
        s.flush()
        txn = Transaction(account_id=acc.id, txn_date=date(2026, 10, 1), description="DINNER", amount_minor=9000,
                          direction=Direction.debit, fingerprint="f")
        s.add(txn)
        s.commit()
        txn_id = txn.id
    con = sqlite3.connect(db)
    con.execute("DROP TABLE splitshare")  # the table as it was before group splits
    con.execute("""CREATE TABLE splitshare (id INTEGER NOT NULL PRIMARY KEY,
                   expense_txn_id INTEGER NOT NULL REFERENCES "transaction" (id),
                   person VARCHAR NOT NULL, amount_minor INTEGER NOT NULL)""")
    con.execute("INSERT INTO splitshare VALUES (1, ?, 'Asha', 5000)", (txn_id,))
    con.execute("PRAGMA user_version = 7")
    con.commit()
    con.close()

    init_db(db_path=db)
    con = sqlite3.connect(db)
    assert con.execute("SELECT id, expense_txn_id, expense_group_id, person, amount_minor FROM splitshare") \
        .fetchall() == [(1, txn_id, None, "Asha", 5000)]
    notnull = {r[1]: r[3] for r in con.execute("PRAGMA table_info(splitshare)")}
    assert notnull["expense_txn_id"] == 0
