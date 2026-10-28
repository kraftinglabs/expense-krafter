"""Queries and edits on transactions: listing, labelling, grouping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, Direction, ExpenseGroup, Label, Transaction, TxnKind


def accounts(active_only: bool = True) -> list[Account]:
    with get_session() as s:
        q = select(Account).order_by(Account.type, Account.name)
        if active_only:
            q = q.where(Account.active == True)  # noqa: E712
        return list(s.exec(q).all())


def labels() -> list[Label]:
    with get_session() as s:
        return list(s.exec(select(Label).order_by(Label.name)).all())


@dataclass
class TxnRow:
    id: int
    txn_date: date
    account: str
    description: str
    amount_minor: int
    direction: Direction
    kind: TxnKind
    label_id: int | None
    group_id: int | None
    group_name: str
    notes: str
    account_id: int = 0


@dataclass
class MonthSummary:
    month: str  # YYYY-MM
    debit_minor: int
    credit_minor: int
    count: int
    unlabelled: int


def _filtered(q, account_id: int | None, unlabelled_only: bool):
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if unlabelled_only:
        q = q.where(Transaction.label_id == None, Transaction.kind == TxnKind.expense)  # noqa: E711
    return q


def _month_bounds(month: str) -> tuple[date, date]:
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)


def month_summaries(account_id: int | None = None, unlabelled_only: bool = False) -> list[MonthSummary]:
    """Newest month first."""
    with get_session() as s:
        rows = s.exec(_filtered(select(Transaction.txn_date, Transaction.amount_minor, Transaction.direction,
                                       Transaction.label_id, Transaction.kind), account_id, unlabelled_only)).all()
    by_month: dict[str, MonthSummary] = {}
    for d, amt, direction, label_id, kind in rows:
        key = d.strftime("%Y-%m")
        ms = by_month.setdefault(key, MonthSummary(key, 0, 0, 0, 0))
        ms.count += 1
        if direction == Direction.debit:
            ms.debit_minor += amt
        else:
            ms.credit_minor += amt
        if label_id is None and kind == TxnKind.expense:
            ms.unlabelled += 1
    return sorted(by_month.values(), key=lambda m: m.month, reverse=True)


def month_transactions(month: str, account_id: int | None = None,
                       unlabelled_only: bool = False) -> list[TxnRow]:
    """Transactions of one month, newest day first."""
    start, end = _month_bounds(month)
    with get_session() as s:
        q = (select(Transaction, Account.name, ExpenseGroup.name)
             .join(Account, Account.id == Transaction.account_id)
             .outerjoin(ExpenseGroup, ExpenseGroup.id == Transaction.group_id)
             .where(Transaction.txn_date >= start, Transaction.txn_date < end))
        rows = s.exec(_filtered(q, account_id, unlabelled_only)
                      .order_by(col(Transaction.txn_date).desc(), Transaction.id)).all()
    return [TxnRow(t.id, t.txn_date, acc, t.description, t.amount_minor, t.direction, t.kind, t.label_id,
                   t.group_id, grp or "", t.notes, t.account_id) for t, acc, grp in rows]


def set_fields(txn_id: int, **fields) -> None:
    """Update label_id / kind / notes on one transaction."""
    allowed = {"label_id", "kind", "notes"}
    if unknown := set(fields) - allowed:
        raise ValueError(f"Not editable: {unknown}")
    with get_session() as s:
        t = s.get(Transaction, txn_id)
        for k, v in fields.items():
            setattr(t, k, TxnKind(v) if k == "kind" else v)
        if "kind" in fields:
            t.kind_manual = True
        s.add(t)
        s.commit()


def group_transactions(txn_ids: list[int], name: str) -> ExpenseGroup:
    with get_session() as s:
        group = ExpenseGroup(name=name)
        s.add(group)
        s.flush()
        for t in s.exec(select(Transaction).where(col(Transaction.id).in_(txn_ids))):
            t.group_id = group.id
            s.add(t)
        s.commit()
        return group


def ungroup_transactions(txn_ids: list[int]) -> None:
    with get_session() as s:
        txns = list(s.exec(select(Transaction).where(col(Transaction.id).in_(txn_ids))))
        group_ids = {t.group_id for t in txns if t.group_id}
        for t in txns:
            t.group_id = None
            s.add(t)
        s.flush()
        # Drop groups that are now empty.
        for gid in group_ids:
            if not s.exec(select(Transaction).where(Transaction.group_id == gid)).first():
                s.delete(s.get(ExpenseGroup, gid))
        s.commit()


