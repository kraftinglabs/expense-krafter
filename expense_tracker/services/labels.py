"""Labels: create with an automatic colour, edit, and delete only when unused."""

from __future__ import annotations

import random

from sqlalchemy import func
from sqlmodel import select

from ..db import get_session
from ..db.models import ExpenseGroup, Frequency, Label, Transaction

# Distinct, mid-tone colours that read on white and as chart series. New labels
# get one at random, preferring colours no other label uses yet.
PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
    "#0f9fb5", "#a0522d", "#7a8c00", "#c2185b", "#5c6bc0", "#00897b", "#8d6e63", "#d4a017",
]


def pick_color(rng: random.Random | None = None) -> str:
    rng = rng or random
    with get_session() as s:
        used = {c.lower() for c in s.exec(select(Label.color)).all()}
    free = [c for c in PALETTE if c.lower() not in used]
    return rng.choice(free or PALETTE)


def usage_count(label_id: int) -> int:
    """Transactions and groups that use the label."""
    with get_session() as s:
        txns = s.exec(select(func.count()).select_from(Transaction).where(Transaction.label_id == label_id)).one()
        groups = s.exec(select(func.count()).select_from(ExpenseGroup)
                        .where(ExpenseGroup.label_id == label_id)).one()
    return txns + groups


def _check_name(name: str, label_id: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    with get_session() as s:
        clash = s.exec(select(Label).where(func.lower(Label.name) == name.lower())).first()
    if clash and clash.id != label_id:
        raise ValueError(f"A label called '{clash.name}' already exists")
    return name


def create_label(name: str, recurring: bool = False, frequency: Frequency | None = None,
                 color: str | None = None) -> Label:
    label = Label(name=_check_name(name), color=color or pick_color(), is_recurring=recurring,
                  frequency=frequency if recurring else None)
    with get_session() as s:
        s.add(label)
        s.commit()
        s.refresh(label)
    return label


def update_label(label_id: int, *, name: str, color: str, recurring: bool,
                 frequency: Frequency | None) -> Label:
    with get_session() as s:
        label = s.get(Label, label_id)
        label.name = _check_name(name, label_id)
        label.color = color
        label.is_recurring = recurring
        label.frequency = frequency if recurring else None
        s.add(label)
        s.commit()
        s.refresh(label)
        return label


def delete_label(label_id: int) -> None:
    if (n := usage_count(label_id)):
        raise ValueError(f"Label is used by {n} transaction{'s' if n != 1 else ''}; it can't be deleted")
    with get_session() as s:
        s.delete(s.get(Label, label_id))
        s.commit()
