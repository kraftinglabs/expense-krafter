"""Credit card statements: what's been paid and what's pending."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func
from sqlmodel import select

from ..db import get_session
from ..db.models import Account, CCPaymentLink, CCStatement, Transaction, TxnKind


@dataclass
class StatementStatus:
    statement: CCStatement
    account_name: str
    paid_minor: int

    @property
    def pending_minor(self) -> int:
        return max(self.statement.total_due_minor - self.paid_minor, 0)

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
    out = [StatementStatus(st, name, paid.get(st.id, 0)) for st, name in rows]
    return [x for x in out if x.pending_minor > 0] if only_pending else out


def due_soon(within_days: int) -> list[StatementStatus]:
    return [x for x in statement_statuses(only_pending=True) if x.days_to_due <= within_days]


def link_payment(statement_id: int, bank_txn_id: int, amount_minor: int) -> None:
    with get_session() as s:
        s.add(CCPaymentLink(statement_id=statement_id, bank_txn_id=bank_txn_id, amount_minor=amount_minor))
        txn = s.get(Transaction, bank_txn_id)
        txn.kind = TxnKind.cc_payment
        s.add(txn)
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
