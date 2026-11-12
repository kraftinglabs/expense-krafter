"""Credit card statements: what's been paid and what's pending.

A bill is paid by bank debits linked to it (CCPaymentLink). When the bank
statement with the payment isn't imported yet, "Mark as paid" records an
expected payment instead: a placeholder transaction (expected=True) on the
bank account it'll come from, linked like a real debit. It counts the bill as
paid, never shows as a transaction or in balances, and is swapped for the real
debit when that bank statement is imported (attach_payment).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func
from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, CCPaymentLink, CCStatement, Direction, Transaction, TxnKind
from .money import fmt


class CardError(ValueError):
    """A card payment that can't be recorded; the message says why."""


@dataclass
class ExpectedPayment:
    """A payment marked by hand, waiting for its bank statement."""

    txn_id: int
    account_id: int
    account: str
    paid_on: date
    amount_minor: int
    note: str


@dataclass
class StatementStatus:
    statement: CCStatement
    account_name: str
    paid_minor: int
    expected: list[ExpectedPayment] = field(default_factory=list)

    @property
    def pending_minor(self) -> int:
        return max(self.statement.total_due_minor - self.paid_minor, 0)

    @property
    def expected_minor(self) -> int:
        return sum(p.amount_minor for p in self.expected)

    @property
    def days_to_due(self) -> int:
        return (self.statement.due_date - date.today()).days

    @property
    def status(self) -> str:
        if self.pending_minor == 0:
            return "paid"
        if self.days_to_due < 0:
            return "overdue"
        return "partially paid" if self.paid_minor else "pending"


def statement_statuses(only_pending: bool = False) -> list[StatementStatus]:
    with get_session() as s:
        paid = dict(s.exec(
            select(CCPaymentLink.statement_id, func.sum(CCPaymentLink.amount_minor))
            .group_by(CCPaymentLink.statement_id)
        ).all())
        rows = s.exec(select(CCStatement, Account.name).join(Account, Account.id == CCStatement.account_id)
                      .order_by(CCStatement.due_date.desc())).all()
        expected: dict[int, list[ExpectedPayment]] = {}
        for link, t, acc in s.exec(
                select(CCPaymentLink, Transaction, Account.name)
                .join(Transaction, Transaction.id == CCPaymentLink.bank_txn_id)
                .join(Account, Account.id == Transaction.account_id)
                .where(Transaction.expected == True)):  # noqa: E712
            expected.setdefault(link.statement_id, []).append(
                ExpectedPayment(t.id, t.account_id, acc, t.txn_date, link.amount_minor, t.notes))
    out = [StatementStatus(st, name, paid.get(st.id, 0), expected.get(st.id, [])) for st, name in rows]
    return [x for x in out if x.pending_minor > 0] if only_pending else out


def due_soon(within_days: int) -> list[StatementStatus]:
    return [x for x in statement_statuses(only_pending=True) if x.days_to_due <= within_days]


def link_payment(statement_id: int, bank_txn_id: int, amount_minor: int) -> None:
    with get_session() as s:
        s.add(CCPaymentLink(statement_id=statement_id, bank_txn_id=bank_txn_id, amount_minor=amount_minor))
        txn = s.get(Transaction, bank_txn_id)
        txn.kind = TxnKind.cc_payment
        txn.kind_manual = True  # you linked it to a card bill yourself
        s.add(txn)
        s.commit()


def usual_payment_account(card_account_id: int) -> int | None:
    """The bank account that paid this card's bills most often, for a default."""
    with get_session() as s:
        return s.exec(select(Transaction.account_id)
                      .join(CCPaymentLink, CCPaymentLink.bank_txn_id == Transaction.id)
                      .join(CCStatement, CCStatement.id == CCPaymentLink.statement_id)
                      .where(CCStatement.account_id == card_account_id)
                      .group_by(Transaction.account_id)
                      .order_by(func.count().desc())).first()


def mark_paid(statement_id: int, bank_account_id: int, paid_on: date, amount_minor: int, note: str = "") -> int:
    """Record a payment you made but whose bank statement isn't imported yet.
    Returns the expected payment's id."""
    status = next((x for x in statement_statuses() if x.statement.id == statement_id), None)
    if status is None:
        raise CardError("That statement no longer exists.")
    if amount_minor <= 0:
        raise CardError("Enter the amount you paid.")
    if amount_minor > status.pending_minor:
        raise CardError(f"Only {fmt(status.pending_minor)} is pending on this bill.")
    with get_session() as s:
        if s.get(Account, bank_account_id) is None:
            raise CardError("Pick the account you paid from.")
        txn = Transaction(account_id=bank_account_id, txn_date=paid_on,
                          description=f"{status.account_name} bill payment", amount_minor=amount_minor,
                          direction=Direction.debit, kind=TxnKind.cc_payment, kind_manual=True,
                          fingerprint=f"expected:{uuid.uuid4().hex}", expected=True, notes=" ".join(note.split()))
        s.add(txn)
        s.flush()
        s.add(CCPaymentLink(statement_id=statement_id, bank_txn_id=txn.id, amount_minor=amount_minor))
        s.commit()
        return txn.id


def unmark_paid(expected_txn_id: int) -> None:
    """Undo a Mark as paid."""
    with get_session() as s:
        txn = s.get(Transaction, expected_txn_id)
        if txn is None or not txn.expected:
            return
        for link in s.exec(select(CCPaymentLink).where(CCPaymentLink.bank_txn_id == expected_txn_id)):
            s.delete(link)
        s.flush()
        s.delete(txn)
        s.commit()


# How far the bank's date may be from the date you entered.
MATCH_DAYS = 7


def payment_candidates(expected_txn_id: int, txn_ids: set[int] | None = None, nearby_only: bool = True
                       ) -> list[Transaction]:
    """Imported debits on the same bank account that could be this payment,
    best first: same amount within MATCH_DAYS (or, with nearby_only=False,
    any debit on that account not linked to a card bill yet)."""
    with get_session() as s:
        expected = s.get(Transaction, expected_txn_id)
        if expected is None or not expected.expected:
            return []
        linked = set(s.exec(select(CCPaymentLink.bank_txn_id)).all())
        q = select(Transaction).where(Transaction.account_id == expected.account_id,
                                      Transaction.direction == Direction.debit,
                                      Transaction.expected == False)  # noqa: E712
        if nearby_only:
            window = timedelta(days=MATCH_DAYS)
            q = q.where(Transaction.amount_minor == expected.amount_minor,
                        Transaction.txn_date >= expected.txn_date - window,
                        Transaction.txn_date <= expected.txn_date + window)
        rows = [t for t in s.exec(q.order_by(col(Transaction.txn_date).desc()))
                if t.id not in linked and (txn_ids is None or t.id in txn_ids)]
    return sorted(rows, key=lambda t: (abs(t.amount_minor - expected.amount_minor),
                                       abs((t.txn_date - expected.txn_date).days)))


def payment_matches(txn_ids: set[int]) -> list[tuple[StatementStatus, ExpectedPayment, list[Transaction]]]:
    """For a fresh bank import: payments marked by hand that look like these new rows."""
    out = []
    for x in statement_statuses():
        for p in x.expected:
            if found := payment_candidates(p.txn_id, txn_ids):
                out.append((x, p, found))
    return out


def attach_payment(expected_txn_id: int, bank_txn_id: int) -> None:
    """Swap a payment marked by hand for the real bank debit."""
    with get_session() as s:
        expected = s.get(Transaction, expected_txn_id)
        txn = s.get(Transaction, bank_txn_id)
        if expected is None or not expected.expected:
            raise CardError("That payment is already attached.")
        if txn is None or txn.expected or txn.direction != Direction.debit:
            raise CardError("Pick a debit from an imported bank statement.")
        if s.exec(select(CCPaymentLink).where(CCPaymentLink.bank_txn_id == bank_txn_id)).first():
            raise CardError("That debit already pays a card bill.")
        for link in s.exec(select(CCPaymentLink).where(CCPaymentLink.bank_txn_id == expected_txn_id)):
            link.bank_txn_id = bank_txn_id
            s.add(link)
        txn.kind, txn.kind_manual = TxnKind.cc_payment, True  # a transfer to the card, not spend
        if not txn.notes:
            txn.notes = expected.notes
        s.add(txn)
        s.flush()
        s.delete(expected)
        s.commit()


def add_statement(account_id: int, statement_date: date, due_date: date, total_due_minor: int,
                  min_due_minor: int = 0) -> CCStatement:
    """Manual entry, until a card's PDF parser can read the summary."""
    with get_session() as s:
        st = CCStatement(account_id=account_id, statement_date=statement_date, due_date=due_date,
                         total_due_minor=total_due_minor, min_due_minor=min_due_minor)
        s.add(st)
        s.commit()
        return st
