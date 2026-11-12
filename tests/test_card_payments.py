from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Transaction, TxnKind
from expensekrafter.services import analytics, cards, ledger, splits
from expensekrafter.services.cards import CardError


@pytest.fixture
def ids(tmp_path):
    """A card with a ₹20,000 bill due 20 Nov, and a bank account whose statement isn't imported yet."""
    init_db(db_path=tmp_path / "c.db")
    with get_session() as s:
        card = Account(name="Card", type=AccountType.credit_card)
        bank = Account(name="Bank", type=AccountType.bank)
        s.add_all([card, bank])
        s.commit()
        card_id, bank_id = card.id, bank.id
    st = cards.add_statement(card_id, date(2026, 11, 1), date(2026, 11, 20), 2_000_000)
    return st.id, card_id, bank_id


def _import_bank_row(bank: int, day: date, minor: int, fp: str) -> int:
    with get_session() as s:
        t = Transaction(account_id=bank, txn_date=day, description="BILLDESK CARD PAYMENT", amount_minor=minor,
                        direction=Direction.debit, kind=TxnKind.expense, fingerprint=fp)
        s.add(t)
        s.commit()
        return t.id


def test_mark_paid_then_attach_the_bank_debit(ids):
    st, card, bank = ids
    expected = cards.mark_paid(st, bank, date(2026, 11, 10), 2_000_000, " via  CRED ")
    [x] = cards.statement_statuses()
    assert (x.status, x.pending_minor, x.expected_minor) == ("paid", 0, 2_000_000)
    assert x.expected[0].note == "via CRED" and cards.due_soon(30) == []

    # The placeholder is no transaction, no spend, and not a pending split.
    assert ledger.filtered_rows(ledger.TxnFilter()) == []
    assert analytics.spend_frame().empty and splits.pending_items() == []

    other = _import_bank_row(bank, date(2026, 11, 11), 1_999_900, "a")
    real = _import_bank_row(bank, date(2026, 11, 12), 2_000_000, "b")
    [(found, pay, candidates)] = cards.payment_matches({other, real})
    assert pay.txn_id == expected and [t.id for t in candidates] == [real]
    cards.attach_payment(expected, real)

    [x] = cards.statement_statuses()
    assert (x.status, x.expected) == ("paid", [])
    with get_session() as s:
        assert s.get(Transaction, expected) is None
        paid = s.get(Transaction, real)
        assert (paid.kind, paid.notes) == (TxnKind.cc_payment, "via CRED")  # a transfer, not spend
    assert analytics.spend_frame()["amount"].tolist() == [19999.0]


def test_mark_paid_rules_and_undo(ids):
    st, card, bank = ids
    with pytest.raises(CardError, match="Only"):
        cards.mark_paid(st, bank, date(2026, 11, 10), 2_500_000)
    partial = cards.mark_paid(st, bank, date(2026, 11, 10), 500_000)
    assert cards.statement_statuses()[0].status == "partially paid"
    with pytest.raises(CardError, match="Only"):
        cards.mark_paid(st, bank, date(2026, 11, 10), 1_600_000)  # ₹15,000 left
    cards.unmark_paid(partial)
    assert cards.statement_statuses()[0].pending_minor == 2_000_000
    far = _import_bank_row(bank, date(2026, 12, 30), 500_000, "far")
    again = cards.mark_paid(st, bank, date(2026, 11, 10), 500_000)
    assert cards.payment_candidates(again) == []  # too far from the date
    assert [t.id for t in cards.payment_candidates(again, nearby_only=False)] == [far]


def test_paid_from_defaults_to_the_usual_account(ids):
    st, card, bank = ids
    assert cards.usual_payment_account(card) is None
    debit = _import_bank_row(bank, date(2026, 11, 5), 2_000_000, "p")
    cards.link_payment(st, debit, 2_000_000)
    assert cards.usual_payment_account(card) == bank
