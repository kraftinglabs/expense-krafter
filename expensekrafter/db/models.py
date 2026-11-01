"""Database schema.

Money is stored as integer minor units (paise/cents) to avoid float rounding.

Core idea: a *transaction* is a raw statement row; an *expense* is what you
actually spent. Grouping, CC-payment links and reimbursement links turn the
former into the latter without double counting:
  - bank debit that pays a card bill  -> kind=cc_payment (a transfer, not spend)
  - card swipes                        -> the real expenses
  - friends paying you back            -> kind=reimbursement, linked to the expense
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class AccountType(str, Enum):
    bank = "bank"
    credit_card = "credit_card"


class Direction(str, Enum):
    debit = "debit"
    credit = "credit"


class TxnKind(str, Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"            # between your own accounts
    cc_payment = "cc_payment"        # bank debit settling a card statement
    reimbursement = "reimbursement"  # money returned for a split expense
    refund = "refund"                # merchant refund / reversal


class IntervalUnit(str, Enum):
    week = "week"
    month = "month"
    year = "year"


class Meta(SQLModel, table=True):
    """Key/value facts about this database, e.g. its unique id."""

    key: str = Field(primary_key=True)
    value: str


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    type: AccountType
    institution: str = ""
    # Only the first 5 characters of the account/card number are kept; the
    # rest is replaced with X before it ever reaches the DB.
    number_masked: str = ""
    parser_key: str = "generic_excel"
    # Bank icon key ("" = pick automatically from the bank name, "generic" = none).
    icon: str = ""
    # Credit cards only: day of month the statement is generated / payment is due.
    statement_day: Optional[int] = None
    due_day: Optional[int] = None
    active: bool = True


class ImportBatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    filename: str
    file_sha256: str = Field(index=True)
    parser_key: str
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    rows_total: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0


class Label(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    color: str = "#4c78a8"
    is_recurring: bool = False
    # Recurring labels repeat every `every` units, e.g. 6 months = half-yearly.
    every: Optional[int] = None
    unit: Optional[IntervalUnit] = None
    # Group label this one rolls up into (SIP -> Investments). One level only:
    # a group can't itself sit in a group.
    parent_id: Optional[int] = Field(default=None, foreign_key="label.id")


class ExpenseGroup(SQLModel, table=True):
    """Several transactions that are really one expense (e.g. a purchase split
    into two payments because of a transaction limit)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    label_id: Optional[int] = Field(default=None, foreign_key="label.id")
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Transaction(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("account_id", "fingerprint"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    import_id: Optional[int] = Field(default=None, foreign_key="importbatch.id")
    txn_date: date = Field(index=True)
    description: str
    amount_minor: int  # always positive; see direction
    direction: Direction
    balance_minor: Optional[int] = None
    ref_no: str = ""
    fingerprint: str = Field(index=True)

    kind: TxnKind = TxnKind.expense
    # True once you set the type yourself; re-applying rules never overrides it.
    kind_manual: bool = False
    label_id: Optional[int] = Field(default=None, foreign_key="label.id")
    group_id: Optional[int] = Field(default=None, foreign_key="expensegroup.id")
    # Card transactions: the statement they were billed on.
    cc_statement_id: Optional[int] = Field(default=None, foreign_key="ccstatement.id")
    notes: str = ""


class SkippedRow(SQLModel, table=True):
    """A statement row you unticked on import. When it turns up again (an
    overlapping export, or a card statement repeating a payment) it starts
    unticked."""

    __table_args__ = (UniqueConstraint("account_id", "fingerprint"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    fingerprint: str
    skipped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CCStatement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    import_id: Optional[int] = Field(default=None, foreign_key="importbatch.id")
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    statement_date: date
    due_date: date
    total_due_minor: int
    min_due_minor: int = 0


class CCPaymentLink(SQLModel, table=True):
    """Allocates (part of) a bank debit to a card statement. Several debits can
    pay one statement, and one debit can be split across statements."""

    id: Optional[int] = Field(default=None, primary_key=True)
    statement_id: int = Field(foreign_key="ccstatement.id", index=True)
    bank_txn_id: int = Field(foreign_key="transaction.id", index=True)
    amount_minor: int


class SplitShare(SQLModel, table=True):
    """What one person owes you for an expense you paid for everyone. Your own
    share is whatever the shares don't cover."""

    id: Optional[int] = Field(default=None, primary_key=True)
    expense_txn_id: int = Field(foreign_key="transaction.id", index=True)
    person: str
    amount_minor: int


class PayrollReimbursement(SQLModel, table=True):
    """Money your employer pays back inside your salary (a meal allowance, an
    internet bill). There's no bank credit of its own to mark, so it's entered
    by hand: spend analysis takes it off its label and income analysis takes it
    off the salary, so it isn't counted as both income and a saving."""

    id: Optional[int] = Field(default=None, primary_key=True)
    paid_on: date = Field(index=True)
    amount_minor: int
    label_id: Optional[int] = Field(default=None, foreign_key="label.id")
    # The account the salary went into (for the dashboard's account filter).
    account_id: Optional[int] = Field(default=None, foreign_key="account.id")
    note: str = ""


class ReimbursementLink(SQLModel, table=True):
    """Links money paid back by others (a bank credit) to the expense it
    reimburses. The expense is either a single transaction or a group."""

    id: Optional[int] = Field(default=None, primary_key=True)
    expense_txn_id: Optional[int] = Field(default=None, foreign_key="transaction.id")
    expense_group_id: Optional[int] = Field(default=None, foreign_key="expensegroup.id")
    credit_txn_id: int = Field(foreign_key="transaction.id", index=True)
    amount_minor: int
    person: str = ""
