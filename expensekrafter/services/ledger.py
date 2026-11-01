"""Queries and edits on transactions: listing, labelling, grouping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import col, select

from ..db import get_session
from ..db.models import (Account, Direction, ExpenseGroup, Label, ReimbursementLink, SplitShare, Transaction,
                         TxnKind)
from . import narration
from .labels import with_children


def accounts(active_only: bool = True) -> list[Account]:
    with get_session() as s:
        q = select(Account).order_by(Account.type, Account.name)
        if active_only:
            q = q.where(Account.active == True)  # noqa: E712
        return list(s.exec(q).all())


def labels() -> list[Label]:
    with get_session() as s:
        return list(s.exec(select(Label).order_by(Label.name)).all())


# Display names for transaction types (also what search matches on).
KIND_LABELS = {
    TxnKind.expense: "Expense", TxnKind.income: "Income", TxnKind.transfer: "Transfer",
    TxnKind.cc_payment: "Card payment", TxnKind.reimbursement: "Reimbursement", TxnKind.refund: "Refund",
}
NO_LABEL = 0  # stands for "no label" in TxnFilter.label_ids


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
    label_name: str = ""


@dataclass
class MonthSummary:
    month: str  # YYYY-MM
    debit_minor: int
    credit_minor: int
    count: int
    unlabelled: int


@dataclass
class TxnFilter:
    """What the Transactions page shows. Empty / None fields don't filter."""
    account_id: int | None = None
    unlabelled_only: bool = False
    search: str = ""
    kinds: set[str] = field(default_factory=set)        # TxnKind values
    label_ids: set[int] = field(default_factory=set)    # label ids, NO_LABEL for unlabelled
    direction: str | None = None                        # "debit" (money out) / "credit" (money in)
    date_from: date | None = None
    date_to: date | None = None                         # inclusive
    amount_min: float | None = None                     # rupees
    amount_max: float | None = None

    def extra_count(self) -> int:
        """How many filters beyond account / search / unlabelled are set (for the button badge)."""
        return sum([bool(self.kinds), bool(self.label_ids), self.direction is not None,
                    self.date_from is not None or self.date_to is not None,
                    self.amount_min is not None or self.amount_max is not None])

    def is_narrowed(self) -> bool:
        return bool(self.search.strip() or self.extra_count() or self.unlabelled_only)


def _where(q, f: TxnFilter):
    q = q.where(Transaction.expected == False)  # noqa: E712  (pending-split placeholders live on Splits)
    if f.account_id:
        q = q.where(Transaction.account_id == f.account_id)
    if f.unlabelled_only:
        q = q.where(Transaction.label_id == None, Transaction.kind == TxnKind.expense)  # noqa: E711
    if f.kinds:
        q = q.where(col(Transaction.kind).in_([TxnKind(k) for k in f.kinds]))
    if f.label_ids:
        ids = list(with_children({i for i in f.label_ids if i != NO_LABEL}))  # a group takes its labels
        cond = col(Transaction.label_id).in_(ids)
        if NO_LABEL in f.label_ids:
            cond = cond | (Transaction.label_id == None)  # noqa: E711
        q = q.where(cond)
    if f.direction:
        q = q.where(Transaction.direction == Direction(f.direction))
    if f.date_from:
        q = q.where(Transaction.txn_date >= f.date_from)
    if f.date_to:
        q = q.where(Transaction.txn_date <= f.date_to)
    if f.amount_min is not None:
        q = q.where(Transaction.amount_minor >= round(f.amount_min * 100))
    if f.amount_max is not None:
        q = q.where(Transaction.amount_minor <= round(f.amount_max * 100))
    return q


def _haystack(t: TxnRow) -> str:
    """Everything a search can match for one row, lower-cased."""
    n = narration.parse(t.description)
    rupees = t.amount_minor / 100
    parts = [t.description, n.display, n.title, n.method, *n.details, n.ref, t.notes, t.label_name,
             t.group_name, t.account, KIND_LABELS.get(t.kind, t.kind.value),
             "money in" if t.direction == Direction.credit else "money out",
             f"{rupees:.2f}", f"{rupees:,.2f}", f"{rupees:,.2f}".replace(",", "")]
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).lower()


def matches_search(t: TxnRow, query: str) -> bool:
    """Every word of the query must appear somewhere ('grab niyo', '1,417', 'upi sbi')."""
    hay = _haystack(t)
    return all(word in hay for word in query.lower().replace("₹", " ").split())


def filtered_rows(f: TxnFilter, month: str | None = None) -> list[TxnRow]:
    """Matching transactions, newest day first; optionally only one month."""
    with get_session() as s:
        q = (select(Transaction, Account.name, ExpenseGroup.name, Label.name)
             .join(Account, Account.id == Transaction.account_id)
             .outerjoin(ExpenseGroup, ExpenseGroup.id == Transaction.group_id)
             .outerjoin(Label, Label.id == Transaction.label_id))
        if month:
            start, end = _month_bounds(month)
            q = q.where(Transaction.txn_date >= start, Transaction.txn_date < end)
        rows = s.exec(_where(q, f).order_by(col(Transaction.txn_date).desc(), Transaction.id)).all()
    out = [TxnRow(t.id, t.txn_date, acc, t.description, t.amount_minor, t.direction, t.kind, t.label_id,
                  t.group_id, grp or "", t.notes, t.account_id, lab or "") for t, acc, grp, lab in rows]
    if f.search.strip():
        out = [t for t in out if matches_search(t, f.search)]
    return out


def _month_bounds(month: str) -> tuple[date, date]:
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)


def month_summaries(f: TxnFilter | None = None) -> list[MonthSummary]:
    """Newest month first."""
    by_month: dict[str, MonthSummary] = {}
    for t in filtered_rows(f or TxnFilter()):
        key = t.txn_date.strftime("%Y-%m")
        ms = by_month.setdefault(key, MonthSummary(key, 0, 0, 0, 0))
        ms.count += 1
        if t.direction == Direction.debit:
            ms.debit_minor += t.amount_minor
        else:
            ms.credit_minor += t.amount_minor
        if t.label_id is None and t.kind == TxnKind.expense:
            ms.unlabelled += 1
    return sorted(by_month.values(), key=lambda m: m.month, reverse=True)


def month_transactions(month: str, f: TxnFilter | None = None) -> list[TxnRow]:
    """Transactions of one month, newest day first."""
    return filtered_rows(f or TxnFilter(), month)


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
    """Take transactions out of their groups (the groups stay). A group with a
    split or repayment on it can't be emptied: delete that split first."""
    with get_session() as s:
        txns = list(s.exec(select(Transaction).where(col(Transaction.id).in_(txn_ids))))
        group_ids = {t.group_id for t in txns if t.group_id}
        for gid in group_ids:
            left = s.exec(select(Transaction).where(Transaction.group_id == gid,
                                                    col(Transaction.id).not_in(txn_ids))).first()
            has_split = (s.exec(select(SplitShare).where(SplitShare.expense_group_id == gid)).first()
                         or s.exec(select(ReimbursementLink).where(ReimbursementLink.expense_group_id == gid)).first())
            if has_split and not left:
                name = s.get(ExpenseGroup, gid).name
                raise ValueError(f"“{name}” has a split; delete it on Splits & reimbursements before ungrouping "
                                 "all of it")
        for t in txns:
            t.group_id = None
            s.add(t)
        s.commit()  # the groups stay, even empty: delete them on the Expense groups page


