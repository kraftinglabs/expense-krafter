"""SBI Card statement parsing. The text mimics the layout pdfplumber extracts
from a real statement; names, numbers and amounts are made up."""

from datetime import date

from expense_tracker.db.models import Direction
from expense_tracker.importers.sbi_card import parse_text
from expense_tracker.services.narration import parse

STATEMENT = """GSTIN of SBI Card : 06AAAAA0000A1ZV Stmt/Debit Note/Credit Note/Tax Invoice
A B CARDHOLDER Credit Card Number
XXXX XXXX XXXX XX99
*Total Amount Due ( ` )
1,050.50
**Minimum Amount Due ( ` )
STMT No. : D00000000000
CKYC No. : 10000000000000 100.00
Credit Limit( ` ) (including cash) Cash Limit ( ` )(as part of credit limit) Statement Date
50,000.00 5,000.00 10 Oct 2026
Available Credit Limit ( ` ) Available Cash Limit ( ` ) Payment Due Date
48,949.50 5,000.00 30 Oct 2026
ACCOUNT SUMMARY
Previous Balance Reversals & other Purchases & Other Fee, Taxes & Total Outstanding
( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )
3,000.00 3,000.00 1,050.50 0.00 1,050.50
Date Transaction Details Amount ( ` )
for Statement Period: 11 Sep 26 to 10 Oct 26
12 Sep 26 PAYMENT RECEIVED 000000000ABCDEF123 3,000.00 C
TRANSACTIONS FOR A B CARDHOLDER
14 Sep 26 ACME FOODS PVT LTD BANGALORE IN 250.50 D
20 Sep 26 WWW EXAMPLE IN SI NEW DELHI IN 300.00 D
28 Sep 26 BIG SPORTS STORE Pune IN (Pay in EMIs) 500.00 D
C=Credit ; D=Debit; EN=Encash; FP=Flexipay
On 17 Sep 26 we revised fees by 100.00 for some cards
"""


def test_transactions_and_directions():
    st = parse_text(STATEMENT)
    assert [(t.txn_date, t.amount_minor, t.direction) for t in st.transactions] == [
        (date(2026, 9, 12), 300000, Direction.credit),
        (date(2026, 9, 14), 25050, Direction.debit),
        (date(2026, 9, 20), 30000, Direction.debit),
        (date(2026, 9, 28), 50000, Direction.debit),
    ]
    assert st.transactions[3].description == "BIG SPORTS STORE Pune IN (Pay in EMIs)"
    assert st.warnings == []  # totals cross-check passed; prose lines were not taken as rows


def test_summary():
    s = parse_text(STATEMENT).card_summary
    assert (s.statement_date, s.due_date, s.total_due_minor, s.min_due_minor) == (
        date(2026, 10, 10), date(2026, 10, 30), 105050, 10000)
    assert (s.period_start, s.period_end) == (date(2026, 9, 11), date(2026, 10, 10))


def test_missing_line_is_flagged_by_totals_check():
    st = parse_text(STATEMENT.replace("14 Sep 26 ACME FOODS PVT LTD BANGALORE IN 250.50 D\n", ""))
    assert any("Debits add up to 800.00" in w for w in st.warnings)


def test_card_rows_read_nicely():
    n = parse("ACME FOODS PVT LTD BANGALORE IN")
    assert (n.title, n.method, n.details) == ("Acme Foods", "Card", ["Bangalore"])
    n = parse("WWW EXAMPLE IN SI NEW DELHI IN")
    assert n.details == ["New Delhi", "Auto-pay"]
    assert parse("BIG SPORTS STORE Pune IN (Pay in EMIs)").details == ["Pune", "Pay in EMIs"]
    assert parse("PAYMENT RECEIVED 000000000ABCDEF123").title == "Payment received"
