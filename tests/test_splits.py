from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Transaction, TxnKind
from expensekrafter.services import analytics, splits
from expensekrafter.services.splits import SplitError


@pytest.fixture
def ids(tmp_path):
    """Made-up rows: a ₹3,000 dinner paid for three, and two friends' credits."""
    init_db(db_path=tmp_path / "s.db")
    with get_session() as s:
        bank = Account(name="Bank", type=AccountType.bank)
        s.add(bank)
        s.flush()
        rows = [  # date, description, rupees, direction, kind
            (date(2026, 9, 5), "UPI/DR/1/DINNER PLACE/x@y", 3000, Direction.debit, TxnKind.expense),
            (date(2026, 10, 2), "UPI/CR/2/FRIEND A/a@y", 1000, Direction.credit, TxnKind.income),
            (date(2026, 10, 3), "UPI/CR/3/FRIEND B/b@y", 1500, Direction.credit, TxnKind.transfer),
        ]
        txns = [Transaction(account_id=bank.id, txn_date=d, description=desc, amount_minor=r * 100, direction=dr,
                            kind=k, fingerprint=f"f{i}") for i, (d, desc, r, dr, k) in enumerate(rows)]
        s.add_all(txns)
        s.commit()
        return [t.id for t in txns]


def test_split_owed_paid_and_spend(ids):
    dinner, from_a, from_b = ids
    splits.save_split(dinner, {"Asha": 100000, "Bala": 100000})
    sp = splits.splits()[0]
    assert (sp.your_share_minor, sp.owed_minor, sp.remaining_minor, sp.settled) == (100000, 200000, 200000, False)

    splits.record_repayment(dinner, from_a, "asha", 100000)  # name matches case-insensitively
    splits.record_repayment(dinner, from_b, "Bala", 60000)   # paid in part; the rest of B's credit stays free
    sp = splits.splits()[0]
    assert [(p.person, p.paid_minor, p.remaining_minor) for p in sp.people] == [("Asha", 100000, 0),
                                                                                ("Bala", 60000, 40000)]
    assert [c.free_minor for c in splits.credit_candidates()] == [90000]  # A's credit is used up

    # Spend: the dinner minus what came back for it; the unassigned ₹900 of B's credit is money back on its own day.
    with get_session() as s:
        assert s.get(Transaction, from_b).kind == TxnKind.reimbursement
    df = analytics.spend_frame()
    assert df["amount"].tolist() == [3000 - 1000 - 600, -900]

    splits.record_repayment(dinner, from_b, "Bala", 40000)
    assert splits.splits()[0].settled
    assert analytics.spend_frame()["amount"].tolist() == [1000, -500]


def test_split_rules(ids):
    dinner, from_a, _ = ids
    with pytest.raises(SplitError, match="more than the expense"):
        splits.save_split(dinner, {"Asha": 200000, "Bala": 200000})
    with pytest.raises(SplitError, match="twice"):
        splits.save_split(dinner, {"Asha": 1000, "asha ": 1000})
    with pytest.raises(SplitError, match="paid out"):
        splits.save_split(from_a, {"Asha": 1000})
    splits.save_split(dinner, {"Asha": 50000})
    with pytest.raises(SplitError, match="only owes"):
        splits.record_repayment(dinner, from_a, "Asha", 60000)
    with pytest.raises(SplitError, match="isn't part"):
        splits.record_repayment(dinner, from_a, "Chitra", 100)
    splits.record_repayment(dinner, from_a, "Asha", 50000)
    with pytest.raises(SplitError, match="already paid back"):
        splits.save_split(dinner, {"Asha": 10000})

    link = splits.splits()[0].repayments[0]
    splits.remove_repayment(link.link_id)
    assert splits.splits()[0].paid_minor == 0
    splits.delete_split(dinner)
    assert splits.splits() == []
    assert splits.people() == []
