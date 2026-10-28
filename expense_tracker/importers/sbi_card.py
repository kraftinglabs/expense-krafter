"""SBI Card monthly statement (PDF).

The statement is text, not drawn tables, so the generic PDF parser finds
nothing. Transactions are single lines:

    11 Sep 26 PAYMENT RECEIVED 000000000DSBI7VJ1VQIZ3P 15,007.00 C
    28 Sep 26 DECATHLON SPORTS INDIA Bangalore IN (Pay in EMIs) 4,455.00 D

Codes after the amount: C credit, D debit, T temporary credit; EN / FP / EMD /
BT / M are Encash, Flexipay, Easy Money Draft, Balance Transfer and monthly
instalments (all debits).

The summary block gives total / minimum due, statement and due dates, and an
account summary whose Payments and Purchases totals are used to check that
every transaction line was read.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..db.models import AccountType, Direction
from .base import CardSummary, ParsedStatement, ParsedTxn, StatementParser, register
from .tabular import open_pdf, parse_money

CREDIT_CODES = {"C", "T"}
CODES = "C|D|T|EN|FP|EMD|BT|M"
AMOUNT = r"\d{1,3}(?:,\d{2,3})*\.\d{2}"

TXN_LINE = re.compile(rf"^(?P<date>\d{{2}} [A-Za-z]{{3}} \d{{2}})\s+(?P<desc>.+?)\s+(?P<amount>{AMOUNT})\s+(?P<code>{CODES})$")
PERIOD = re.compile(r"Statement Period:\s*(\d{2} [A-Za-z]{3} \d{2})\s+to\s+(\d{2} [A-Za-z]{3} \d{2})")
LONG_DATE = r"\d{1,2} [A-Za-z]{3} \d{4}"


def _date(text: str) -> date:
    fmt = "%d %b %y" if len(text.split()[-1]) == 2 else "%d %b %Y"
    return datetime.strptime(text, fmt).date()


def _after(label: str, text: str, what: str) -> str | None:
    """First `what` (regex) found after `label`; SBI prints values a line or
    two below their labels."""
    m = re.search(rf"{label}.*?({what})", text, re.S | re.I)
    return m.group(1) if m else None


def parse_text(text: str) -> ParsedStatement:
    out = ParsedStatement()
    for line in text.splitlines():
        m = TXN_LINE.match(line.strip())
        if not m:
            continue
        out.transactions.append(ParsedTxn(
            txn_date=_date(m["date"]),
            description=m["desc"].strip(),
            amount_minor=parse_money(m["amount"]),
            direction=Direction.credit if m["code"] in CREDIT_CODES else Direction.debit,
        ))

    total = _after(r"Total Amount Due", text, AMOUNT)
    minimum = _after(r"Minimum Amount Due", text, AMOUNT)
    stmt_date = _after(r"Statement Date", text, LONG_DATE)
    due_date = _after(r"Payment Due Date", text, LONG_DATE)
    period = PERIOD.search(text)
    if total and stmt_date and due_date:
        out.card_summary = CardSummary(
            statement_date=_date(stmt_date), due_date=_date(due_date),
            total_due_minor=parse_money(total), min_due_minor=parse_money(minimum) if minimum else 0,
            period_start=_date(period[1]) if period else None, period_end=_date(period[2]) if period else None,
        )
    else:
        out.warnings.append("Couldn't read the statement summary (total due / dates); add it on the Credit cards page.")

    # Cross-check against the account summary: Previous balance, Payments &
    # credits, Purchases & debits, Fees & interest, Total outstanding.
    summary = re.search(rf"ACCOUNT SUMMARY.*?\n\s*({AMOUNT})\s+({AMOUNT})\s+({AMOUNT})\s+({AMOUNT})\s+({AMOUNT})",
                        text, re.S)
    if summary:
        credits = sum(t.amount_minor for t in out.transactions if t.direction == Direction.credit)
        debits = sum(t.amount_minor for t in out.transactions if t.direction == Direction.debit)
        want_credits, want_debits, fees = (parse_money(summary[i]) for i in (2, 3, 4))
        if credits != want_credits:
            out.warnings.append(f"Credits add up to {credits / 100:,.2f} but the statement says "
                                f"{want_credits / 100:,.2f}; some lines may not have been read.")
        if debits not in (want_debits, want_debits + fees):
            out.warnings.append(f"Debits add up to {debits / 100:,.2f} but the statement says "
                                f"{want_debits / 100:,.2f}; some lines may not have been read.")

    if not out.transactions:
        out.warnings.append("No transaction lines found. Is this an SBI Card statement?")
    return out


@register
class SbiCardParser(StatementParser):
    key = "sbi_card_pdf"
    display_name = "SBI Card statement (PDF)"
    account_type = AccountType.credit_card
    extensions = (".pdf",)

    def parse(self, path: Path, password: str | None = None) -> ParsedStatement:
        with open_pdf(path, password) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return parse_text(text)
