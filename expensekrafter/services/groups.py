"""Expense groups: several transactions that are one expense or one occasion
(a trip, a purchase paid in two parts, a wedding). Groups are made on purpose
and last until you delete them; transactions are added and removed any time.
A card expense whose statement isn't imported yet can be put in a group as an
expected item (see splits.create_pending); it joins for real when matched.
A group can be split or reimbursed as a whole (splits.save_split(group_id=)).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timezone

from sqlalchemy import func
from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, Direction, ExpenseGroup, Label, ReimbursementLink, SplitShare, Transaction
from . import splits
from .analytics import merchant_name


class GroupError(ValueError):
    """A group change that can't be made; the message says why."""


@dataclass
class Item:
    txn_id: int
    txn_date: date
    name: str
    description: str
    account: str
    amount_minor: int
    direction: Direction
    expected: bool  # a card expense still waiting for its statement


@dataclass
class Group:
    id: int
    name: str
    label_id: int | None
    label: str
    color: str | None
    notes: str
    created: date
    items: list[Item] = field(default_factory=list)
    split: splits.Split | None = None  # its split / reimbursement, if any

    @property
    def count(self) -> int:
        return sum(not i.expected for i in self.items)

    @property
    def waiting(self) -> int:
        return sum(i.expected for i in self.items)

    @property
    def total_minor(self) -> int:
        """Paid out less refunds, including expected items."""
        return sum(i.amount_minor if i.direction == Direction.debit else -i.amount_minor for i in self.items)

    @property
    def first_date(self) -> date | None:
        return min((i.txn_date for i in self.items), default=None)

    @property
    def last_date(self) -> date | None:
        return max((i.txn_date for i in self.items), default=None)


def _check_name(name: str, group_id: int | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise GroupError("Give the group a name, e.g. “Vietnam trip, Nov 2026”.")
    with get_session() as s:
        clash = s.exec(select(ExpenseGroup).where(func.lower(ExpenseGroup.name) == name.lower())).first()
    if clash and clash.id != group_id:
        raise GroupError(f"There's already a group called “{clash.name}”.")
    return name


def create_group(name: str, label_id: int | None = None, notes: str = "") -> int:
    with get_session() as s:
        group = ExpenseGroup(name=_check_name(name), label_id=label_id, notes=notes.strip())
        s.add(group)
        s.commit()
        return group.id


def update_group(group_id: int, *, name: str, label_id: int | None, notes: str = "") -> None:
    name = _check_name(name, group_id)
    with get_session() as s:
        group = s.get(ExpenseGroup, group_id)
        if group is None:
            raise GroupError("That group no longer exists.")
        group.name, group.label_id, group.notes = name, label_id, notes.strip()
        s.add(group)
        s.commit()


def _has_split(s, group_id: int) -> bool:
    return bool(s.exec(select(SplitShare).where(SplitShare.expense_group_id == group_id)).first()
                or s.exec(select(ReimbursementLink).where(ReimbursementLink.expense_group_id == group_id)).first())


def add_to_group(txn_ids: list[int], group_id: int) -> int:
    """Move transactions into a group (out of any other one). Returns how many moved."""
    with get_session() as s:
        if s.get(ExpenseGroup, group_id) is None:
            raise GroupError("That group no longer exists.")
        txns = [t for t in s.exec(select(Transaction).where(col(Transaction.id).in_(txn_ids)))
                if t.group_id != group_id]
        # Don't empty another group that has a split on it.
        for old in {t.group_id for t in txns if t.group_id}:
            moving = {t.id for t in txns if t.group_id == old}
            left = s.exec(select(func.count()).select_from(Transaction)
                          .where(Transaction.group_id == old, col(Transaction.id).not_in(moving))).one()
            if not left and _has_split(s, old):
                raise GroupError(f"That would empty “{s.get(ExpenseGroup, old).name}”, which has a split; "
                                 "delete the split first.")
        for t in txns:
            t.group_id = group_id
            s.add(t)
        s.commit()
        return len(txns)


def remove_from_group(txn_ids: list[int]) -> None:
    """Take transactions out of their group (the group stays). An expected item
    with no split of its own is deleted: it only existed for the group."""
    from .ledger import ungroup_transactions

    try:
        ungroup_transactions(txn_ids)
    except ValueError as exc:
        raise GroupError(str(exc)) from exc
    with get_session() as s:
        for t in s.exec(select(Transaction).where(col(Transaction.id).in_(txn_ids), Transaction.expected == True)):  # noqa: E712
            if not s.exec(select(SplitShare).where(SplitShare.expense_txn_id == t.id)).first():
                s.delete(t)
        s.commit()


def delete_group(group_id: int) -> None:
    """Delete a group; its transactions stay, ungrouped. Not while it has a split."""
    with get_session() as s:
        group = s.get(ExpenseGroup, group_id)
        if group is None:
            return
        if _has_split(s, group_id):
            raise GroupError(f"“{group.name}” has a split / reimbursement; delete that first.")
        for t in s.exec(select(Transaction).where(Transaction.group_id == group_id)):
            if t.expected and not s.exec(select(SplitShare).where(SplitShare.expense_txn_id == t.id)).first():
                s.delete(t)
            else:
                t.group_id = None
                s.add(t)
        s.flush()
        s.delete(group)
        s.commit()


def add_expected(group_id: int, account_id: int, txn_date: date, amount_minor: int, description: str,
                 label_id: int | None = None) -> int:
    """A card expense for this group whose statement isn't imported yet."""
    try:
        return splits.create_pending(account_id, txn_date, amount_minor, description, {}, label_id, group_id)
    except splits.SplitError as exc:
        raise GroupError(str(exc)) from exc


def options() -> dict[int, str]:
    """Group id → name, newest first (for pickers)."""
    return {g.id: g.name for g in groups()}


def groups() -> list[Group]:
    """Every group with its transactions, the most recently active first."""
    by_group = {sp.group_id: sp for sp in splits.splits() if sp.group_id}
    with get_session() as s:
        rows = s.exec(select(ExpenseGroup, Label.name, Label.color)
                      .join(Label, Label.id == ExpenseGroup.label_id, isouter=True)).all()
        members = splits.group_members(s, [g.id for g, _, _ in rows])
        acc_names = {a.id: a.name for a in s.exec(select(Account))}
    out = []
    for g, lab, color in rows:
        items = [Item(t.id, t.txn_date, t.description if t.expected else merchant_name(t.description),
                      t.description, acc_names.get(t.account_id, ""), t.amount_minor, t.direction, t.expected)
                 for t in members.get(g.id, [])]
        created = g.created_at.replace(tzinfo=timezone.utc).astimezone().date()  # stored in UTC
        out.append(Group(g.id, g.name, g.label_id, lab or "", color, g.notes, created, items,
                         by_group.get(g.id)))
    return sorted(out, key=lambda g: (g.last_date or g.created, g.id), reverse=True)
