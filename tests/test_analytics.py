from datetime import date

from expensekrafter.db.models import Account, AccountType, Direction, TxnKind
from expensekrafter.services.analytics import merchant_name, period_for
from expensekrafter.services.importer import default_kind


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
    from expensekrafter.db import get_session, init_db
    from expensekrafter.db.models import Transaction

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
    from expensekrafter.db import get_session
    from expensekrafter.db.models import ReimbursementLink
    from expensekrafter.services import analytics

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
    from expensekrafter.db import init_db
    from expensekrafter.services import analytics, labels

    init_db(db_path=tmp_path / "a.db")
    lab = labels.create_label("Insurance", recurring=True, every=6)
    today = date.today()
    _db_with(tmp_path, [(today, "ACKO POLICY", 1200, Direction.debit, TxnKind.expense, lab.id)])
    items = analytics.recurring_items()
    assert items.iloc[0]["frequency"] == "Half-yearly" and items.iloc[0]["yearly"] == 2400


def test_balance_history_starts_from_the_opening_balance(tmp_path):
    from expensekrafter.db import get_session, init_db
    from expensekrafter.db.models import Transaction
    from expensekrafter.services import analytics

    init_db(db_path=tmp_path / "b.db")
    with get_session() as s:
        a, b = Account(name="SBI", type=AccountType.bank), Account(name="DCB", type=AccountType.bank)
        s.add_all([a, b])
        s.flush()
        rows = [(a, date(2026, 1, 5), Direction.debit, 1000, 99000),   # opened at 1,00,000
                (a, date(2026, 1, 5), Direction.credit, 500, 99500),   # same day: last row wins
                (a, date(2026, 1, 20), Direction.debit, 9500, 90000),
                (b, date(2026, 1, 10), Direction.credit, 5000, 25000)]  # opened at 20,000
        s.add_all(Transaction(account_id=acc.id, txn_date=d, description="x", amount_minor=amt * 100,
                              direction=dr, balance_minor=bal * 100, fingerprint=f"f{i}")
                  for i, (acc, d, dr, amt, bal) in enumerate(rows))
        s.commit()
    daily = analytics.daily_balances(analytics.balance_history(), until=date(2026, 1, 31))
    assert analytics.balance_on(daily, date(2026, 1, 4)) == 100000 + 20000
    assert analytics.balance_on(daily, date(2026, 1, 5)) == 99500 + 20000
    assert analytics.balance_on(daily, date(2026, 1, 31)) == 90000 + 25000
    assert analytics.balance_on(daily, date(2025, 12, 1)) is None


def test_label_totals_folds_the_tail_into_miscellaneous():
    import pandas as pd

    from expensekrafter.services.analytics import MISC, label_totals

    amounts = {"Rent": 900, "Food": 500, "Travel": 300, "Fuel": 200, "Health": 60, "Books": 30, "Games": 10}
    df = pd.DataFrame([{"label": k, "color": "#000", "amount": v} for k, v in amounts.items()])
    top = label_totals(df, top=5)
    assert top["label"].tolist() == ["Rent", "Food", "Travel", "Fuel", "Health", MISC]
    assert top["amount"].iloc[-1] == 40 and round(top["share"].sum()) == 100
    assert len(label_totals(df)) == 7
    assert MISC not in label_totals(df.head(6), top=5)["label"].tolist()  # one extra label isn't folded


def test_comparable_previous_cuts_a_running_period():
    from expensekrafter.services.analytics import Period, comparable_previous

    oct_ = Period(date(2026, 10, 1), date(2026, 11, 1))
    assert comparable_previous(oct_, today=date(2026, 10, 10)) == Period(date(2026, 9, 1), date(2026, 9, 11))
    done = Period(date(2026, 9, 1), date(2026, 10, 1))
    assert comparable_previous(done, today=date(2026, 10, 10)) == done.previous()


def test_label_changes_and_daily_spend():
    import pandas as pd

    from expensekrafter.services.analytics import daily_spend, label_changes

    def frame(rows):
        return pd.DataFrame([{"label": lab, "amount": amt, "date": pd.Timestamp(d)} for lab, amt, d in rows])

    now = frame([("Food", 900, "2026-10-01"), ("Food", 100, "2026-10-01"), ("Travel", 200, "2026-10-03"),
                 ("Rent", 500, "2026-10-03")])
    before = frame([("Food", 400, "2026-09-01"), ("Travel", 700, "2026-09-02"), ("Rent", 500, "2026-09-03")])
    ch = label_changes(now, before)
    assert ch["label"].tolist() == ["Food", "Travel"]  # unchanged Rent is left out
    assert ch["change"].tolist() == [600, -500]
    days = daily_spend(now, date(2026, 10, 1), date(2026, 10, 5))
    assert days["amount"].tolist() == [1000, 0, 700, 0] and days["count"].tolist() == [2, 0, 2, 0]


def test_single_month_periods():
    oct_ = period_for("2026-10")
    assert (oct_.start, oct_.end) == (date(2026, 10, 1), date(2026, 11, 1))
    assert (oct_.previous().start, oct_.previous().end) == (date(2026, 9, 1), date(2026, 10, 1))
    dec = period_for("2026-12")
    assert dec.end == date(2027, 1, 1) and dec.previous().start == date(2026, 11, 1)
    q = period_for("3m", today=date(2026, 10, 15))  # Aug-Oct compares with May-Jul
    assert (q.previous().start, q.previous().end) == (date(2026, 5, 1), date(2026, 8, 1))
    assert period_for("2026-13") is None
