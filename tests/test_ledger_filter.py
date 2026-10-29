from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Label, Transaction, TxnKind
from expensekrafter.services import ledger
from expensekrafter.services.ledger import NO_LABEL, TxnFilter

D, C = Direction.debit, Direction.credit


@pytest.fixture
def ids(tmp_path):
    """Made-up rows across two accounts and two months."""
    init_db(db_path=tmp_path / "f.db")
    with get_session() as s:
        bank, card = Account(name="Bank", type=AccountType.bank), Account(name="Travel Card", type=AccountType.bank)
        food = Label(name="Food & Dining", color="#e45756")
        s.add_all([bank, card, food])
        s.flush()
        rows = [  # account, date, description, rupees, direction, kind, label, notes
            (bank, date(2026, 9, 3), "UPI/DR/111/SWIGGY/YESB/swiggy@ybl/Food", 450.00, D, TxnKind.expense, food, ""),
            (bank, date(2026, 9, 20), "NEFT*HDFC0000001*N1*ACME CORP", 50000.00, C, TxnKind.income, None, ""),
            (card, date(2026, 10, 5), "POS VS/612345678901/12:19:55/Grab* A-1AB/XYZ", 1417.90, D, TxnKind.expense,
             None, "airport ride"),
            (card, date(2026, 10, 6), "POS VS REV/612345678901/12:19:55/Grab* /", 75.10, C, TxnKind.refund, None, ""),
            (bank, date(2026, 10, 9), "UPI/DR/222/DCB BANK/DCBL/someone@x/NO R", 5000.00, D, TxnKind.transfer,
             None, ""),
        ]
        txns = [Transaction(account_id=a.id, txn_date=d, description=desc, amount_minor=round(r * 100),
                            direction=dr, kind=k, label_id=lab.id if lab else None, notes=n, fingerprint=f"f{i}")
                for i, (a, d, desc, r, dr, k, lab, n) in enumerate(rows)]
        s.add_all(txns)
        s.commit()
        return {"bank": bank.id, "food": food.id, "txns": [t.id for t in txns]}


def found(f: TxnFilter) -> list[str]:
    return [t.description.split("/")[0] + ":" + str(t.amount_minor // 100) for t in ledger.filtered_rows(f)]


def test_search_matches_names_details_notes_labels_and_amounts(ids):
    assert len(found(TxnFilter(search="grab"))) == 2               # cleaned-up name
    assert found(TxnFilter(search="airport")) == ["POS VS:1417"]   # notes
    assert found(TxnFilter(search="food")) == ["UPI:450"]          # label name
    assert found(TxnFilter(search="1,417")) == ["POS VS:1417"]     # formatted amount
    assert found(TxnFilter(search="₹1417.90")) == ["POS VS:1417"]
    assert found(TxnFilter(search="acme")) == ["NEFT*HDFC0000001*N1*ACME CORP:50000"]
    assert found(TxnFilter(search="travel card refund")) == ["POS VS REV:75"]  # all words must match
    assert found(TxnFilter(search="grab bank")) == []


def test_field_filters(ids):
    assert len(found(TxnFilter(kinds={"expense"}))) == 2
    assert len(found(TxnFilter(kinds={"refund", "transfer"}))) == 2
    assert found(TxnFilter(label_ids={ids["food"]})) == ["UPI:450"]
    assert len(found(TxnFilter(label_ids={NO_LABEL}))) == 4
    assert len(found(TxnFilter(label_ids={NO_LABEL, ids["food"]}))) == 5
    assert len(found(TxnFilter(direction="credit"))) == 2
    assert len(found(TxnFilter(date_from=date(2026, 10, 1)))) == 3
    assert len(found(TxnFilter(date_from=date(2026, 9, 3), date_to=date(2026, 9, 3)))) == 1  # inclusive
    assert found(TxnFilter(amount_min=1000, amount_max=5000)) == ["UPI:5000", "POS VS:1417"]  # newest first
    assert len(found(TxnFilter(account_id=ids["bank"], direction="debit"))) == 2


def test_months_follow_the_filter(ids):
    f = TxnFilter(search="grab")
    assert [m.month for m in ledger.month_summaries(f)] == ["2026-10"]
    assert len(ledger.month_transactions("2026-10", f)) == 2
    assert [m.month for m in ledger.month_summaries()] == ["2026-10", "2026-09"]


def test_filter_badge_counts():
    assert TxnFilter().extra_count() == 0 and not TxnFilter().is_narrowed()
    f = TxnFilter(kinds={"expense"}, date_to=date(2026, 1, 1), amount_min=10, search="x")
    assert f.extra_count() == 3 and f.is_narrowed()
