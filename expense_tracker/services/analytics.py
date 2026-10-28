"""Spend analysis for the dashboard.

"Spend" = what you actually paid:
  + expense debits (bank and card)
  - reimbursements (friends paying you back): taken off the expense they are
    linked to, or off the month they arrived in when not linked (yet)
  - merchant refunds (credits marked kind=refund)
"Income" = credits marked kind=income (salary, interest...), kept separate so
income vs spend and the savings rate can be shown.
Card bill payments and transfers between your own accounts are neither, so
nothing is counted twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlmodel import select

from ..db import get_session
from ..db.models import Account, Direction, Label, ReimbursementLink, Transaction, TxnKind
from . import labels, narration

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


def _frame(where, period: Period | None, account_id: int | None):
    with get_session() as s:
        q = (select(Transaction, Account.name, Label.name, Label.color)
             .join(Account, Account.id == Transaction.account_id)
             .outerjoin(Label, Label.id == Transaction.label_id)
             .where(where))
        if period:
            q = q.where(Transaction.txn_date >= period.start, Transaction.txn_date < period.end)
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        return s.exec(q).all()


def _record(t: Transaction, acc: str, lab: str | None, color: str | None, amount_minor: int) -> dict:
    return {"id": t.id, "date": pd.Timestamp(t.txn_date), "account": acc,
            "label": lab or UNLABELLED, "color": color or UNLABELLED_COLOR,
            "merchant": merchant_name(t.description), "description": t.description,
            "amount": amount_minor / 100}


def spend_frame(period: Period | None = None, account_id: int | None = None) -> pd.DataFrame:
    """One row per spend item, in rupees. Refunds and reimbursements not linked
    to an expense are negative rows on the day the money came back."""
    rows = _frame(((Transaction.kind == TxnKind.expense) & (Transaction.direction == Direction.debit))
                  | (Transaction.kind == TxnKind.refund)
                  | ((Transaction.kind == TxnKind.reimbursement) & (Transaction.direction == Direction.credit)),
                  period, account_id)
    with get_session() as s:
        links = s.exec(select(ReimbursementLink)).all()
    off_expense: dict[int, int] = {}  # expense txn id -> reimbursed amount
    from_credit: dict[int, int] = {}  # credit txn id -> amount taken off a single expense
    for link in links:
        if link.expense_txn_id is not None:
            off_expense[link.expense_txn_id] = off_expense.get(link.expense_txn_id, 0) + link.amount_minor
            from_credit[link.credit_txn_id] = from_credit.get(link.credit_txn_id, 0) + link.amount_minor

    records = []
    for t, acc, lab, color in rows:
        if t.kind == TxnKind.refund:
            amount = -t.amount_minor
        elif t.kind == TxnKind.reimbursement:
            # Whatever part isn't already taken off a linked expense.
            amount = -(t.amount_minor - from_credit.get(t.id, 0))
            if not amount:
                continue
        else:
            amount = t.amount_minor - off_expense.get(t.id, 0)
        records.append(_record(t, acc, lab, color, amount))
    return pd.DataFrame(records, columns=COLUMNS)


def income_frame(period: Period | None = None, account_id: int | None = None) -> pd.DataFrame:
    """One row per income credit (salary, interest...), in rupees."""
    rows = _frame((Transaction.kind == TxnKind.income) & (Transaction.direction == Direction.credit),
                  period, account_id)
    return pd.DataFrame([_record(t, acc, lab, color, t.amount_minor) for t, acc, lab, color in rows],
                        columns=COLUMNS)


def monthly_totals(df: pd.DataFrame, period: Period | None, from_first_data: bool = True) -> pd.Series:
    """Sum per calendar month, with empty months as 0 (so a quiet month shows
    as a dip, not as a missing point). Starts at the first month with data
    (months before your first statement aren't 'no spend', just unknown) and
    stops at the current month."""
    if df.empty and (from_first_data or not period):
        return pd.Series(dtype=float)
    if period:
        first = pd.Timestamp(period.start)
        last = pd.Timestamp(min(period.end - timedelta(days=1), date.today()))
    else:
        first, last = df["date"].min(), df["date"].max()
    if from_first_data:
        first = max(first, df["date"].min())
    months = pd.period_range(first, last, freq="M")
    sums = df.groupby(df["date"].dt.to_period("M"))["amount"].sum() if not df.empty else pd.Series(dtype=float)
    return sums.reindex(months, fill_value=0.0).astype(float)


def daily_totals(df: pd.DataFrame, period: Period) -> pd.Series:
    """Sum per day over the period so far, empty days as 0."""
    last = min(period.end - timedelta(days=1), date.today())
    days = pd.date_range(period.start, max(last, period.start), freq="D")
    sums = df.groupby(df["date"].dt.normalize())["amount"].sum() if not df.empty else pd.Series(dtype=float)
    return sums.reindex(days, fill_value=0.0).astype(float)


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
                   Label.name, Label.every, Label.unit)
            .join(Label, Label.id == Transaction.label_id)
            .where(Label.is_recurring == True, Transaction.txn_date >= since,  # noqa: E712
                   Transaction.direction == Direction.debit)
            .order_by(Transaction.txn_date)
        ).all()
    if not rows:
        return pd.DataFrame(columns=["merchant", "label", "frequency", "last_date", "last_amount",
                                     "times", "yearly"])
    df = pd.DataFrame([{"merchant": merchant_name(desc), "label": lab,
                        "frequency": labels.interval_name(every, unit),
                        "per_year": labels.times_per_year(every, unit), "date": d, "amount": amt / 100}
                       for d, desc, amt, lab, every, unit in rows])
    out = (df.groupby(["merchant", "label", "frequency", "per_year"], as_index=False)
             .agg(last_date=("date", "max"), last_amount=("amount", "last"), times=("amount", "size")))
    out["yearly"] = out["last_amount"] * out["per_year"]
    return out.sort_values("yearly", ascending=False)
