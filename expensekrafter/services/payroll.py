"""Reimbursements paid inside your salary (meal allowance, internet bill...).

They arrive as part of the salary credit, so there's nothing in the statement
to mark. Each one is entered by hand; analytics takes it off the spend for its
label and off income (see PayrollReimbursement).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlmodel import select

from ..db import get_session
from ..db.models import Account, Label, PayrollReimbursement, Transaction, TxnKind


class PayrollError(ValueError):
    """An entry that can't be saved; the message says why."""


@dataclass
class Entry:
    id: int
    paid_on: date
    amount_minor: int
    label_id: int | None
    label: str | None
    color: str | None
    account_id: int | None
    account: str | None
    note: str


def entries() -> list[Entry]:
    """Newest first."""
    with get_session() as s:
        rows = s.exec(select(PayrollReimbursement, Label.name, Label.color, Account.name)
                      .outerjoin(Label, Label.id == PayrollReimbursement.label_id)
                      .outerjoin(Account, Account.id == PayrollReimbursement.account_id)
                      .order_by(PayrollReimbursement.paid_on.desc(), PayrollReimbursement.id.desc())).all()
    return [Entry(p.id, p.paid_on, p.amount_minor, p.label_id, lab, color, p.account_id, acc, p.note)
            for p, lab, color, acc in rows]


def save(entry_id: int | None, paid_on: date, amount_minor: int, label_id: int | None,
         account_id: int | None, note: str = "") -> int:
    if amount_minor <= 0:
        raise PayrollError("Enter the amount reimbursed")
    if label_id is None:
        raise PayrollError("Pick the label it pays back (e.g. Food & Dining)")
    with get_session() as s:
        p = s.get(PayrollReimbursement, entry_id) if entry_id else PayrollReimbursement(paid_on=paid_on,
                                                                                        amount_minor=0)
        if p is None:
            raise PayrollError("That entry no longer exists")
        p.paid_on, p.amount_minor, p.label_id, p.account_id, p.note = (paid_on, amount_minor, label_id,
                                                                      account_id, note.strip())
        s.add(p)
        s.commit()
        return p.id


def delete(entry_id: int) -> None:
    with get_session() as s:
        if p := s.get(PayrollReimbursement, entry_id):
            s.delete(p)
            s.commit()


def salary_account() -> int | None:
    """The account the latest income credit went into: where the salary lands."""
    with get_session() as s:
        return s.exec(select(Transaction.account_id).where(Transaction.kind == TxnKind.income)
                      .order_by(Transaction.txn_date.desc())).first()
