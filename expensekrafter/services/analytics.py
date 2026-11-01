"""Spend analysis for the dashboard.

"Spend" = what you actually paid:
  + expense debits (bank and card)
  - reimbursements (friends paying you back): taken off the expense they are
    linked to, or off the month they arrived in when not linked (yet)
  - merchant refunds (credits marked kind=refund)
  - reimbursements paid inside your salary (entered by hand), off their label
"Income" = credits marked kind=income (salary, interest...), kept separate so
income vs spend and the savings rate can be shown. Reimbursements paid inside
the salary are taken off income too: they pay back spending, they aren't earnings.
Card bill payments and transfers between your own accounts are neither, so
nothing is counted twice.

Labels inside a group label count as the group ("SIP" -> "Investments", in the
group's colour); the label itself is kept in the `sublabel` column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import aliased
from sqlmodel import select

from ..db import get_session
from ..db.models import Account, Direction, Label, PayrollReimbursement, ReimbursementLink, Transaction, TxnKind
from . import labels, narration

UNLABELLED = "Unlabelled"
UNLABELLED_COLOR = "#a9aeab"
PAID_IN_SALARY = "Reimbursed in salary"
COLUMNS = ["id", "date", "account", "label", "color", "merchant", "description", "amount", "sublabel"]
Group = aliased(Label)

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
        """The same length just before. Whole months compare with whole months
        (October with September, not with the 31 days before it)."""
        if self.start.day == 1 and self.end.day == 1:
            months = (self.end.year - self.start.year) * 12 + self.end.month - self.start.month
            return Period(_month_start(self.start, months), self.start)
        return Period(self.start - timedelta(days=self.days), self.start)


MONTH_KEY = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _month_start(d: date, back: int = 0) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - back, 12)  # back < 0 goes forward
    return date(y, m + 1, 1)


def period_for(key: str, today: date | None = None) -> Period | None:
    """Presets: this_month, last_month, 3m, 6m, 12m, ytd, all (None), or one
    month as "YYYY-MM"."""
    if MONTH_KEY.fullmatch(key):
        start = date(int(key[:4]), int(key[5:]), 1)
        return Period(start, _month_start(start, -1))
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
    """(transaction, account, label or its group, colour, label) rows."""
    with get_session() as s:
        q = (select(Transaction, Account.name, Label.name, Label.color, Group.name, Group.color)
             .join(Account, Account.id == Transaction.account_id)
             .outerjoin(Label, Label.id == Transaction.label_id)
             .outerjoin(Group, Group.id == Label.parent_id)
             .where(where))
        if period:
            q = q.where(Transaction.txn_date >= period.start, Transaction.txn_date < period.end)
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        return [(t, acc, group or lab, group_color or color, lab)
                for t, acc, lab, color, group, group_color in s.exec(q).all()]


def _record(t: Transaction, acc: str, lab: str | None, color: str | None, sub: str | None,
            amount_minor: int) -> dict:
    return {"id": t.id, "date": pd.Timestamp(t.txn_date), "account": acc,
            "label": lab or UNLABELLED, "color": color or UNLABELLED_COLOR,
            "merchant": merchant_name(t.description), "description": t.description,
            "amount": amount_minor / 100, "sublabel": sub or UNLABELLED}


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
    for t, acc, lab, color, sub in rows:
        if t.kind == TxnKind.refund:
            amount = -t.amount_minor
        elif t.kind == TxnKind.reimbursement:
            # Whatever part isn't already taken off a linked expense.
            amount = -(t.amount_minor - from_credit.get(t.id, 0))
            if not amount:
                continue
        else:
            amount = t.amount_minor - off_expense.get(t.id, 0)
        records.append(_record(t, acc, lab, color, sub, amount))
    records += _payroll_records(period, account_id)
    return pd.DataFrame(records, columns=COLUMNS)


def _payroll_records(period: Period | None, account_id: int | None) -> list[dict]:
    """Reimbursements paid inside the salary, as negative rows on the day they were paid."""
    with get_session() as s:
        q = (select(PayrollReimbursement, Account.name, Label.name, Label.color, Group.name, Group.color)
             .outerjoin(Account, Account.id == PayrollReimbursement.account_id)
             .outerjoin(Label, Label.id == PayrollReimbursement.label_id)
             .outerjoin(Group, Group.id == Label.parent_id))
        if period:
            q = q.where(PayrollReimbursement.paid_on >= period.start, PayrollReimbursement.paid_on < period.end)
        if account_id:
            q = q.where(PayrollReimbursement.account_id == account_id)
        rows = s.exec(q).all()
    return [{"id": None, "date": pd.Timestamp(p.paid_on), "account": acc or "",
             "label": group or lab or UNLABELLED, "color": group_color or color or UNLABELLED_COLOR,
             "merchant": PAID_IN_SALARY, "description": p.note, "amount": -p.amount_minor / 100,
             "sublabel": lab or UNLABELLED} for p, acc, lab, color, group, group_color in rows]


def income_frame(period: Period | None = None, account_id: int | None = None) -> pd.DataFrame:
    """One row per income credit (salary, interest...), in rupees."""
    rows = _frame((Transaction.kind == TxnKind.income) & (Transaction.direction == Direction.credit),
                  period, account_id)
    records = [_record(t, acc, lab, color, sub, t.amount_minor) for t, acc, lab, color, sub in rows]
    return pd.DataFrame(records + _payroll_records(period, account_id), columns=COLUMNS)


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


MISC = "Miscellaneous"
MISC_COLOR = "#c3c8c5"


def label_totals(df: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    """Spend per label, biggest first (label, color, amount, share). With `top`,
    only the `top` biggest labels are kept and the rest become one
    'Miscellaneous' row at the end."""
    if df.empty:
        return pd.DataFrame(columns=["label", "color", "amount", "share"])
    totals = (df.groupby("label", as_index=False).agg(color=("color", "first"), amount=("amount", "sum"))
              .sort_values("amount", ascending=False, ignore_index=True))
    if top is not None and len(totals) > top + 1:  # folding a single label would hide it for nothing
        rest = totals.iloc[top:]
        misc = pd.DataFrame([{"label": MISC, "color": MISC_COLOR, "amount": rest["amount"].sum()}])
        totals = pd.concat([totals.iloc[:top], misc], ignore_index=True)
    spent = totals["amount"].sum()
    totals["share"] = totals["amount"] / spent * 100 if spent else 0.0
    return totals


def comparable_previous(period: Period, today: date | None = None) -> Period:
    """The period to compare `period` with. For a period still running, only the
    same number of days of the previous one, so a half-finished month isn't
    measured against a whole one."""
    today = today or date.today()
    prev = period.previous()
    elapsed = (min(period.end, today + timedelta(days=1)) - period.start).days
    if elapsed >= period.days:  # finished: compare whole with whole
        return prev
    return Period(prev.start, prev.start + timedelta(days=max(elapsed, 1)))


def label_changes(now: pd.DataFrame, before: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """Spend per label now vs before (label, now, before, change), the `n`
    biggest moves either way, biggest rise first."""
    cols = ["label", "now", "before", "change"]
    sums = [f.groupby("label")["amount"].sum() if not f.empty else pd.Series(dtype=float) for f in (now, before)]
    out = pd.concat(sums, axis=1, keys=["now", "before"]).fillna(0.0)
    out["change"] = out["now"] - out["before"]
    out = out[out["change"].round() != 0]
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.loc[out["change"].abs().sort_values(ascending=False).index[:n]]
    return out.sort_values("change", ascending=False).rename_axis("label").reset_index()[cols]


def daily_spend(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """One row per day from start to end (exclusive): date, amount, count."""
    days = pd.date_range(start, end - timedelta(days=1), freq="D")
    if df.empty:
        return pd.DataFrame({"date": days, "amount": 0.0, "count": 0})
    g = df.groupby(df["date"].dt.normalize())["amount"].agg(["sum", "size"])
    g = g.reindex(days, fill_value=0)
    return pd.DataFrame({"date": days, "amount": g["sum"].astype(float).values, "count": g["size"].values})


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


def balance_history(account_id: int | None = None) -> pd.DataFrame:
    """End-of-day balance per bank account, from the running balance printed on
    each statement row. Columns: date, account, balance (rupees). Each account
    also gets a row for the day before its first transaction holding the
    opening balance (the first row's balance with that row undone), so the
    money you already had is never assumed to be zero."""
    with get_session() as s:
        q = (select(Transaction.txn_date, Transaction.direction, Transaction.amount_minor,
                    Transaction.balance_minor, Account.name)
             .join(Account, Account.id == Transaction.account_id)
             .where(Transaction.balance_minor != None)  # noqa: E711
             .order_by(Transaction.account_id, Transaction.txn_date, Transaction.id))
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        rows = s.exec(q).all()
    if not rows:
        return pd.DataFrame(columns=["date", "account", "balance"])
    df = pd.DataFrame(rows, columns=["date", "direction", "amount", "balance", "account"])
    df["date"] = pd.to_datetime(df["date"])
    openings = []
    for acc, part in df.groupby("account", sort=False):
        first = part.iloc[0]
        undo = first["amount"] if first["direction"] == Direction.debit else -first["amount"]
        openings.append({"date": first["date"] - pd.Timedelta(days=1), "account": acc,
                         "balance": first["balance"] + undo})
    eod = df.groupby(["account", "date"], as_index=False, sort=False).last()[["date", "account", "balance"]]
    out = pd.concat([pd.DataFrame(openings), eod], ignore_index=True)
    out["balance"] = out["balance"] / 100
    return out.sort_values(["account", "date"], ignore_index=True)


def daily_balances(history: pd.DataFrame, until: date | None = None) -> pd.DataFrame:
    """One column per account, one row per day, carried forward over days with
    no transactions. Before an account's first statement its opening balance is
    used, so totals across accounts that start on different dates add up."""
    if history.empty:
        return pd.DataFrame()
    wide = history.pivot_table(index="date", columns="account", values="balance", aggfunc="last")
    end = pd.Timestamp(until or date.today())
    days = pd.date_range(wide.index.min(), max(end, wide.index.max()), freq="D")
    return wide.reindex(days).ffill().bfill()


def balance_on(daily: pd.DataFrame, day: date) -> float | None:
    """Total balance at the end of `day`, or None if it's before any data."""
    if daily.empty or pd.Timestamp(day) < daily.index.min():
        return None
    return float(daily.loc[:pd.Timestamp(day)].iloc[-1].sum())
