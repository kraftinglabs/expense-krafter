"""Spend analysis for the dashboard.

"Spend" = what you actually paid:
  + expense debits (bank and card)
  - reimbursements linked to them (friends paying you back)
  - merchant refunds (credits marked kind=refund)
Card bill payments, transfers and income are excluded, so nothing is counted
twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlmodel import select

from ..db import get_session
from ..db.models import (Account, Direction, Frequency, Label, ReimbursementLink, Transaction,
                         TxnKind)
from . import narration

UNLABELLED = "Unlabelled"
UNLABELLED_COLOR = "#a9aeab"
COLUMNS = ["id", "date", "account", "label", "color", "merchant", "description", "amount"]

_NOISE = re.compile(
    r"^(upi|pos|neft|imps|rtgs|ach|nach|ecom|vps|ipay|bil|mmt|atw|nfs|inb)[\s\-/:]+|"
    r"\b\w*\d\w*\b|[@/].*$|[^a-z&\s]", re.IGNORECASE)


def merchant_name(description: str) -> str:
    """Who the money went to / came from. Uses the description rules when one
    matches ('UPI-SWIGGY-1234@ybl' -> 'Swiggy'), else a rough clean-up."""
    if (n := narration.parse(description)).parsed:
        return n.title
    text = _NOISE.sub(" ", n.text)
    text = _NOISE.sub(" ", text)  # second pass for nested prefixes like "UPI/POS"
    words = text.split()
    return " ".join(words[:3]).title() if words else description.strip()[:30]


@dataclass
class Period:
    start: date
    end: date  # exclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def previous(self) -> "Period":
        return Period(self.start - timedelta(days=self.days), self.start)


def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def period_for(key: str, today: date | None = None) -> Period | None:
    """Presets: this_month, last_month, 3m, 6m, 12m, ytd, all (None)."""
    today = today or date.today()
    next_month = _month_start(today, -1)
    match key:
        case "this_month":
            return Period(_month_start(today), next_month)
        case "last_month":
            return Period(_month_start(today, 1), _month_start(today))
        case "3m" | "6m" | "12m":
            return Period(_month_start(today, int(key[:-1]) - 1), next_month)
        case "ytd":
            return Period(date(today.year, 1, 1), next_month)
    return None


def spend_frame(period: Period | None = None, account_id: int | None = None) -> pd.DataFrame:
    """One row per spend item (refunds as negative amounts), in rupees."""
    with get_session() as s:
        q = (select(Transaction, Account.name, Label.name, Label.color)
             .join(Account, Account.id == Transaction.account_id)
             .outerjoin(Label, Label.id == Transaction.label_id)
             .where(((Transaction.kind == TxnKind.expense) & (Transaction.direction == Direction.debit))
                    | (Transaction.kind == TxnKind.refund)))
        if period:
            q = q.where(Transaction.txn_date >= period.start, Transaction.txn_date < period.end)
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        rows = s.exec(q).all()
        reimbursed: dict[int, int] = {}
        for link in s.exec(select(ReimbursementLink).where(ReimbursementLink.expense_txn_id != None)):  # noqa: E711
            reimbursed[link.expense_txn_id] = reimbursed.get(link.expense_txn_id, 0) + link.amount_minor

    records = []
    for t, acc, lab, color in rows:
        amount = t.amount_minor - reimbursed.get(t.id, 0)
        if t.kind == TxnKind.refund:
            amount = -t.amount_minor
        records.append({
            "id": t.id, "date": pd.Timestamp(t.txn_date), "account": acc,
            "label": lab or UNLABELLED, "color": color or UNLABELLED_COLOR,
            "merchant": merchant_name(t.description), "description": t.description,
            "amount": amount / 100,
        })
    return pd.DataFrame(records, columns=COLUMNS)


def top_labels(df: pd.DataFrame, n: int = 7) -> pd.DataFrame:
    """Keep the n biggest labels; fold the rest into 'Other' so the chart never
    needs more colours than the palette has."""
    if df.empty:
        return df
    totals = df.groupby("label")["amount"].sum().sort_values(ascending=False)
    keep = set(totals.index[:n])
    out = df.copy()
    other = ~out["label"].isin(keep)
    out.loc[other, "label"] = "Other"
    out.loc[other, "color"] = "#d5d9d6"
    return out


def recurring_items(months: int = 12) -> pd.DataFrame:
    """Transactions under recurring labels, collapsed per merchant: last
    amount, last date, how often it was seen and the yearly cost estimate."""
    since = _month_start(date.today(), months - 1)
    with get_session() as s:
        rows = s.exec(
            select(Transaction.txn_date, Transaction.description, Transaction.amount_minor,
                   Label.name, Label.frequency)
            .join(Label, Label.id == Transaction.label_id)
            .where(Label.is_recurring == True, Transaction.txn_date >= since,  # noqa: E712
                   Transaction.direction == Direction.debit)
            .order_by(Transaction.txn_date)
        ).all()
    if not rows:
        return pd.DataFrame(columns=["merchant", "label", "frequency", "last_date", "last_amount",
                                     "times", "yearly"])
    df = pd.DataFrame([{"merchant": merchant_name(desc), "label": lab,
                        "frequency": (freq or Frequency.monthly).value, "date": d, "amount": amt / 100}
                       for d, desc, amt, lab, freq in rows])
    per_year = {"monthly": 12, "quarterly": 4, "yearly": 1}
    out = (df.groupby(["merchant", "label", "frequency"], as_index=False)
             .agg(last_date=("date", "max"), last_amount=("amount", "last"), times=("amount", "size")))
    out["yearly"] = out["last_amount"] * out["frequency"].map(per_year)
    return out.sort_values("yearly", ascending=False)
