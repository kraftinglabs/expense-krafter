from datetime import date

from expense_tracker.db.models import Account, AccountType, Direction, TxnKind
from expense_tracker.services.analytics import merchant_name, period_for
from expense_tracker.services.importer import _default_kind


def test_card_bill_payments_are_not_spend():
    bank = Account(name="b", type=AccountType.bank)
    for desc in ("ICICI CC PAYMENT", "BIL/ONL/HDFC CREDIT CARD BILL", "CRED CLUB UPI", "PAYMENT TO CREDIT CARD 4321"):
        assert _default_kind(bank, Direction.debit, desc) == TxnKind.cc_payment, desc
    for desc in ("UPI-SWIGGY", "POS 1234XXXX AMAZON", "CARDAMOM RESTAURANT"):
        assert _default_kind(bank, Direction.debit, desc) == TxnKind.expense, desc


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
