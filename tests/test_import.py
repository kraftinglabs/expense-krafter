import pandas as pd
import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Transaction
from expensekrafter.services.importer import commit_import, preview_import
from sqlmodel import select


@pytest.fixture
def account_id(tmp_path):
    init_db(db_path=tmp_path / "test.db")
    with get_session() as s:
        acc = Account(name="Test Bank", type=AccountType.bank)
        s.add(acc)
        s.commit()
        return acc.id


def _write_export(path, rows):
    # Mimic a bank export: info block above the table.
    header = [["Statement of account", None, None, None, None], [None] * 5,
              ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    pd.DataFrame(header + rows).to_excel(path, header=False, index=False)


def test_overlapping_exports_are_deduplicated(tmp_path, account_id):
    jan = tmp_path / "jan.xlsx"
    _write_export(jan, [
        ["01/01/2026", "UPI-SWIGGY", "250.00", None, "9,750.00"],
        ["02/01/2026", "UPI-AMAZON PART 1", "1,00,000.00", None, "-90,250.00"],
        ["05/01/2026", "SALARY", None, "50,000.00", "-40,250.00"],
    ])
    p = preview_import(account_id, jan, "generic_excel")
    assert len(p.rows) == 3 and p.duplicate_count == 0
    commit_import(p)

    # Second export overlaps the first by two rows.
    overlap = tmp_path / "overlap.xlsx"
    _write_export(overlap, [
        ["02/01/2026", "UPI-AMAZON PART 1", "1,00,000.00", None, "-90,250.00"],
        ["05/01/2026", "SALARY", None, "50,000.00", "-40,250.00"],
        ["06/01/2026", "UPI-SWIGGY", "250.00", None, "-40,500.00"],
    ])
    p2 = preview_import(account_id, overlap, "generic_excel")
    assert p2.duplicate_count == 2 and len(p2.new_rows) == 1
    commit_import(p2)

    with get_session() as s:
        txns = s.exec(select(Transaction)).all()
    assert len(txns) == 4
    salary = next(t for t in txns if t.description == "SALARY")
    assert salary.direction == Direction.credit and salary.amount_minor == 5_000_000


def test_identical_rows_without_balance_are_kept(tmp_path, account_id):
    f = tmp_path / "card.csv"
    f.write_text("Date,Description,Amount\n03/01/2026,STARBUCKS,300.00\n03/01/2026,STARBUCKS,300.00\n")
    p = preview_import(account_id, f, "generic_excel")
    assert len(p.new_rows) == 2
    commit_import(p)
    assert preview_import(account_id, f, "generic_excel").duplicate_count == 2


def test_reapply_rule_types_respects_manual_choices(account_id):
    from datetime import date as d

    from expensekrafter.db.models import TxnKind
    from expensekrafter.services.importer import reapply_rule_types
    from expensekrafter.services.ledger import set_fields

    bill = "WDL TFR   INB SBICARD FOR BILLDESK      SBICARD Payments   0097691162095 AT 12345 MG ROAD"
    with get_session() as s:
        rows = [
            Transaction(account_id=account_id, txn_date=d(2026, 1, 1), description=bill, amount_minor=100,
                        direction=Direction.debit, kind=TxnKind.expense, fingerprint="a"),
            Transaction(account_id=account_id, txn_date=d(2026, 1, 2), description=bill, amount_minor=200,
                        direction=Direction.debit, kind=TxnKind.expense, fingerprint="b"),
            Transaction(account_id=account_id, txn_date=d(2026, 1, 3), description=bill, amount_minor=300,
                        direction=Direction.debit, kind=TxnKind.transfer, fingerprint="c"),
            Transaction(account_id=account_id, txn_date=d(2026, 1, 4), description="UPI-SWIGGY-1@ybl",
                        amount_minor=400, direction=Direction.debit, kind=TxnKind.expense, fingerprint="e"),
        ]
        s.add_all(rows)
        s.commit()
        ids = [r.id for r in rows]
    set_fields(ids[1], kind="expense")  # you said: this one really is an expense

    preview = reapply_rule_types()
    assert [c.txn_id for c in preview] == [ids[0]]          # dry run lists only the untouched default row
    with get_session() as s:
        assert s.get(Transaction, ids[0]).kind == TxnKind.expense  # nothing written yet

    reapply_rule_types(apply=True)
    with get_session() as s:
        kinds = [s.get(Transaction, i).kind for i in ids]
    assert kinds == [TxnKind.cc_payment, TxnKind.expense, TxnKind.transfer, TxnKind.expense]
    assert reapply_rule_types() == []  # idempotent


def test_unticked_rows_are_skipped_and_remembered(tmp_path, account_id):
    f = tmp_path / "stmt.csv"
    f.write_text("Date,Description,Amount\n01/01/2026,COFFEE,100.00\n02/01/2026,CARD PAYMENT RECEIVED,5000.00\n"
                 "03/01/2026,BOOKS,300.00\n")
    p = preview_import(account_id, f, "generic_excel")
    coffee, payment, books = (r.fingerprint for r in p.rows)
    commit_import(p, selected={coffee, books})  # untick the payment

    with get_session() as s:
        assert sorted(t.description for t in s.exec(select(Transaction))) == ["BOOKS", "COFFEE"]

    # The same rows turn up again (e.g. next, overlapping statement)
    p2 = preview_import(account_id, f, "generic_excel")
    by_fp = {r.fingerprint: r for r in p2.rows}
    assert by_fp[payment].skipped_before and not by_fp[payment].duplicate
    assert by_fp[coffee].duplicate
    commit_import(p2)  # default: skipped rows stay out
    with get_session() as s:
        assert len(s.exec(select(Transaction)).all()) == 2

    # Changed your mind: ticking it imports it and forgets the skip
    p3 = preview_import(account_id, f, "generic_excel")
    commit_import(p3, selected={payment})
    p4 = preview_import(account_id, f, "generic_excel")
    assert all(r.duplicate and not r.skipped_before for r in p4.rows)


def test_reimporting_a_card_statement_does_not_duplicate_the_bill(tmp_path, account_id):
    from expensekrafter.db.models import CCStatement
    from expensekrafter.importers.base import CardSummary
    from datetime import date as d

    f = tmp_path / "card.csv"
    f.write_text("Date,Description,Amount\n01/01/2026,COFFEE,100.00\n")
    for _ in range(2):
        p = preview_import(account_id, f, "generic_excel")
        p.statement.card_summary = CardSummary(statement_date=d(2026, 1, 10), due_date=d(2026, 1, 30),
                                               total_due_minor=10000)
        commit_import(p)
    with get_session() as s:
        assert len(s.exec(select(CCStatement)).all()) == 1


def test_import_history_and_where_each_account_is_up_to(tmp_path, account_id):
    from expensekrafter.services import importer

    with get_session() as s:
        s.add(Account(name="Empty Card", type=AccountType.credit_card))
        s.commit()
    f = tmp_path / "feb.xlsx"
    _write_export(f, [["01/02/2026", "UPI-SWIGGY", "250.00", None, "9,750.00"],
                      ["03/02/2026", "UPI-ZOMATO", "100.00", None, "9,650.00"]])
    commit_import(preview_import(account_id, f, "generic_excel"))

    [h] = importer.import_history()
    assert (h.account, h.batch.filename, h.batch.rows_new) == ("Test Bank", "feb.xlsx", 2)
    by_name = {st.account.name: st for st in importer.account_statuses()}
    bank = by_name["Test Bank"]
    assert (bank.txn_count, bank.last_txn.description, bank.last_import.id) == (2, "UPI-ZOMATO", h.batch.id)
    assert by_name["Empty Card"].last_txn is None and by_name["Empty Card"].txn_count == 0
