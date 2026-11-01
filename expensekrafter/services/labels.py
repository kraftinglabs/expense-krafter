"""Labels: create with an automatic colour, edit, group, and delete only when unused."""

from __future__ import annotations

import random

from sqlalchemy import func
from sqlmodel import col, select

from ..db import get_session
from ..db.models import ExpenseGroup, IntervalUnit, Label, PayrollReimbursement, Transaction

# Distinct, mid-tone colours that read on white and as chart series. New labels
# get one at random, preferring colours no other label uses yet.
PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
    "#0f9fb5", "#a0522d", "#7a8c00", "#c2185b", "#5c6bc0", "#00897b", "#8d6e63", "#d4a017",
]


# Intervals offered as one-click choices; any other "every N weeks/months/years"
# can be set as a custom interval.
PRESETS: dict[str, tuple[int, IntervalUnit]] = {
    "Weekly": (1, IntervalUnit.week),
    "Monthly": (1, IntervalUnit.month),
    "Every 2 months": (2, IntervalUnit.month),
    "Quarterly": (3, IntervalUnit.month),
    "Half-yearly": (6, IntervalUnit.month),
    "Yearly": (1, IntervalUnit.year),
}
_PER_YEAR = {IntervalUnit.week: 52, IntervalUnit.month: 12, IntervalUnit.year: 1}


def interval_name(every: int | None, unit: IntervalUnit | None) -> str:
    """'Half-yearly' for (6, month), 'Every 5 weeks' for anything without a name."""
    every, unit = every or 1, IntervalUnit(unit or IntervalUnit.month)
    for name, preset in PRESETS.items():
        if preset == (every, unit):
            return name
    return f"Every {every} {unit.value}{'s' if every != 1 else ''}"


def times_per_year(every: int | None, unit: IntervalUnit | None) -> float:
    return _PER_YEAR[IntervalUnit(unit or IntervalUnit.month)] / (every or 1)


def _interval(recurring: bool, every: int | None, unit: IntervalUnit | None) -> tuple:
    if not recurring:
        return None, None
    every = 1 if every is None else int(every)
    if every < 1:
        raise ValueError("The interval must be at least 1")
    return every, IntervalUnit(unit or IntervalUnit.month)


def pick_color(rng: random.Random | None = None) -> str:
    rng = rng or random
    with get_session() as s:
        used = {c.lower() for c in s.exec(select(Label.color)).all()}
    free = [c for c in PALETTE if c.lower() not in used]
    return rng.choice(free or PALETTE)


def usage_count(label_id: int) -> int:
    """Transactions, groups and salary reimbursements that use the label."""
    with get_session() as s:
        txns = s.exec(select(func.count()).select_from(Transaction).where(Transaction.label_id == label_id)).one()
        groups = s.exec(select(func.count()).select_from(ExpenseGroup)
                        .where(ExpenseGroup.label_id == label_id)).one()
        payroll = s.exec(select(func.count()).select_from(PayrollReimbursement)
                         .where(PayrollReimbursement.label_id == label_id)).one()
    return txns + groups + payroll


def _check_name(name: str, label_id: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    with get_session() as s:
        clash = s.exec(select(Label).where(func.lower(Label.name) == name.lower())).first()
    if clash and clash.id != label_id:
        raise ValueError(f"A label called '{clash.name}' already exists")
    return name


def _check_parent(parent_id: int | None, label_id: int | None = None) -> int | None:
    """Groups are one level deep: the parent can't be in a group itself, and a
    label that already holds others can't go into one."""
    if parent_id is None:
        return None
    if parent_id == label_id:
        raise ValueError("A label can't be its own group")
    with get_session() as s:
        parent = s.get(Label, parent_id)
        if parent is None:
            raise ValueError("That group label no longer exists")
        if parent.parent_id is not None:
            raise ValueError(f"'{parent.name}' is itself inside a group; pick a top-level label")
        if label_id is not None and s.exec(select(Label).where(Label.parent_id == label_id)).first():
            raise ValueError("This label is a group for other labels, so it can't go into a group")
    return parent_id


def with_children(label_ids: set[int]) -> set[int]:
    """The labels plus every label grouped under them."""
    if not label_ids:
        return set()
    with get_session() as s:
        kids = s.exec(select(Label.id).where(col(Label.parent_id).in_(label_ids))).all()
    return set(label_ids) | set(kids)


def create_label(name: str, recurring: bool = False, every: int | None = 1,
                 unit: IntervalUnit | None = IntervalUnit.month, color: str | None = None,
                 parent_id: int | None = None) -> Label:
    every, unit = _interval(recurring, every, unit)
    label = Label(name=_check_name(name), color=color or pick_color(), is_recurring=recurring,
                  every=every, unit=unit, parent_id=_check_parent(parent_id))
    with get_session() as s:
        s.add(label)
        s.commit()
        s.refresh(label)
    return label


def update_label(label_id: int, *, name: str, color: str, recurring: bool,
                 every: int | None = 1, unit: IntervalUnit | None = IntervalUnit.month,
                 parent_id: int | None = None) -> Label:
    every, unit = _interval(recurring, every, unit)
    parent_id = _check_parent(parent_id, label_id)
    with get_session() as s:
        label = s.get(Label, label_id)
        label.name = _check_name(name, label_id)
        label.color = color
        label.is_recurring = recurring
        label.every, label.unit = every, unit
        label.parent_id = parent_id
        s.add(label)
        s.commit()
        s.refresh(label)
        return label


def set_parent(label_id: int, parent_id: int | None) -> Label:
    """Move a label into a group (or out of one, with None)."""
    parent_id = _check_parent(parent_id, label_id)
    with get_session() as s:
        label = s.get(Label, label_id)
        label.parent_id = parent_id
        s.add(label)
        s.commit()
        s.refresh(label)
        return label


def delete_label(label_id: int) -> None:
    if (n := usage_count(label_id)):
        raise ValueError(f"Label is used by {n} transaction{'s' if n != 1 else ''}; it can't be deleted")
    with get_session() as s:
        for child in s.exec(select(Label).where(Label.parent_id == label_id)):
            child.parent_id = None  # its labels stay, just ungrouped
            s.add(child)
        s.delete(s.get(Label, label_id))
        s.commit()
