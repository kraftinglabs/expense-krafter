from datetime import date

from expense_tracker.db.models import Account, AccountType, Direction, TxnKind
from expense_tracker.services.analytics import merchant_name, period_for
from expense_tracker.services.importer import default_kind


def test_card_bill_payments_are_not_spend():
    bank = Account(name="b", type=AccountType.bank)
    for desc in ("ICICI CC PAYMENT", "BIL/ONL/HDFC CREDIT CARD BILL", "CRED CLUB UPI", "PAYMENT TO CREDIT CARD 4321"):
        assert default_kind(bank, Direction.debit, desc) == TxnKind.cc_payment, desc
    for desc in ("UPI-SWIGGY", "POS 1234XXXX AMAZON", "CARDAMOM RESTAURANT"):
        assert default_kind(bank, Direction.debit, desc) == TxnKind.expense, desc


def test_merchant_name_strips_payment_noise():
    assert merchant_name("UPI-SWIGGY-1234567@ybl") == "Swiggy"
    assert merchant_name("POS 4321XXXX AMAZON PAY INDIA") == "Amazon Pay India"
    assert merchant_name("NETFLIX") == "Netflix"


def test_period_presets():
    today = date(2026, 1, 15)
    assert period_for("this_month", today).start == date(2026, 1, 1)
    p = period_for("3m", today)
    assert (p.start, p.end) == (date(2025, 11, 1), date(2026, 2, 1))
    assert period_for("last_month", today).start == date(2025, 12, 1)
    assert period_for("all", today) is None


def _db_with(tmp_path, rows):
    """rows: (date, description, rupees, direction, kind, label_id)."""
    from expense_tracker.db import get_session, init_db
    from expense_tracker.db.models import Transaction

    init_db(db_path=tmp_path / "a.db")
    with get_session() as s:
        acc = Account(name="Bank", type=AccountType.bank)
        s.add(acc)
        s.flush()
        txns = [Transaction(account_id=acc.id, txn_date=d, description=desc, amount_minor=int(r * 100),
                            direction=dr, kind=k, label_id=lab, fingerprint=f"f{i}")
                for i, (d, desc, r, dr, k, lab) in enumerate(rows)]
        s.add_all(txns)
        s.commit()
        return [t.id for t in txns]


def test_spend_counts_reimbursements_and_keeps_income_separate(tmp_path):
    from expense_tracker.db import get_session
    from expense_tracker.db.models import ReimbursementLink
    from expense_tracker.services import analytics

    D, C = Direction.debit, Direction.credit
    ids = _db_with(tmp_path, [
        (date(2026, 1, 5), "DINNER", 3000, D, TxnKind.expense, None),
        (date(2026, 1, 6), "FRIEND A", 1000, C, TxnKind.reimbursement, None),  # linked to dinner
        (date(2026, 2, 2), "FRIEND B", 500, C, TxnKind.reimbursement, None),   # not linked yet
        (date(2026, 1, 31), "SALARY", 50000, C, TxnKind.income, None),
        (date(2026, 1, 20), "TO OWN FD", 9000, D, TxnKind.transfer, None),
        (date(2026, 1, 21), "CARD BILL", 4000, D, TxnKind.cc_payment, None),
    ])
    with get_session() as s:
        s.add(ReimbursementLink(expense_txn_id=ids[0], credit_txn_id=ids[1], amount_minor=100000))
        s.commit()
    spend = analytics.spend_frame()
    assert spend["amount"].sum() == 3000 - 1000 - 500
    assert sorted(spend["amount"]) == [-500, 2000]  # dinner net of its link; the other off February
    income = analytics.income_frame()
    assert income["amount"].sum() == 50000
    period = analytics.Period(date(2026, 1, 1), date(2026, 4, 1))
    months = analytics.monthly_totals(spend, period)
    assert len(months) == 3 and months.iloc[2] == 0  # March has no spend but still shows


def test_half_yearly_subscription_yearly_cost(tmp_path):
    from expense_tracker.db import init_db
    from expense_tracker.services import analytics, labels

    init_db(db_path=tmp_path / "a.db")
    lab = labels.create_label("Insurance", recurring=True, every=6)
    today = date.today()
    _db_with(tmp_path, [(today, "ACKO POLICY", 1200, Direction.debit, TxnKind.expense, lab.id)])
    items = analytics.recurring_items()
    assert items.iloc[0]["frequency"] == "Half-yearly" and items.iloc[0]["yearly"] == 2400
