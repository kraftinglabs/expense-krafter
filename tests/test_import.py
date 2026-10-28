import pandas as pd
import pytest

from expense_tracker.db import get_session, init_db
from expense_tracker.db.models import Account, AccountType, Direction, Transaction
from expense_tracker.services.importer import commit_import, preview_import
from sqlmodel import select


@pytest.fixture
def account_id(tmp_path):
    init_db(db_path=tmp_path / "test.db")
    with get_session() as s:
        acc = Account(name="Test Bank", type=AccountType.bank)
        s.add(acc)
        s.commit()
        return acc.id


def _write_export(path, rows):
    # Mimic a bank export: info block above the table.
    header = [["Statement of account", None, None, None, None], [None] * 5,
              ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    pd.DataFrame(header + rows).to_excel(path, header=False, index=False)


def test_overlapping_exports_are_deduplicated(tmp_path, account_id):
    jan = tmp_path / "jan.xlsx"
    _write_export(jan, [
        ["01/01/2026", "UPI-SWIGGY", "250.00", None, "9,750.00"],
        ["02/01/2026", "UPI-AMAZON PART 1", "1,00,000.00", None, "-90,250.00"],
        ["05/01/2026", "SALARY", None, "50,000.00", "-40,250.00"],
    ])
    p = preview_import(account_id, jan, "generic_excel")
    assert len(p.rows) == 3 and p.duplicate_count == 0
    commit_import(p)

    # Second export overlaps the first by two rows.
    overlap = tmp_path / "overlap.xlsx"
    _write_export(overlap, [
        ["02/01/2026", "UPI-AMAZON PART 1", "1,00,000.00", None, "-90,250.00"],
        ["05/01/2026", "SALARY", None, "50,000.00", "-40,250.00"],
        ["06/01/2026", "UPI-SWIGGY", "250.00", None, "-40,500.00"],
    ])
    p2 = preview_import(account_id, overlap, "generic_excel")
    assert p2.duplicate_count == 2 and len(p2.new_rows) == 1
    commit_import(p2)

    with get_session() as s:
        txns = s.exec(select(Transaction)).all()
    assert len(txns) == 4
    salary = next(t for t in txns if t.description == "SALARY")
    assert salary.direction == Direction.credit and salary.amount_minor == 5_000_000


def test_identical_rows_without_balance_are_kept(tmp_path, account_id):
    f = tmp_path / "card.csv"
    f.write_text("Date,Description,Amount\n03/01/2026,STARBUCKS,300.00\n03/01/2026,STARBUCKS,300.00\n")
    p = preview_import(account_id, f, "generic_excel")
    assert len(p.new_rows) == 2
    commit_import(p)
    assert preview_import(account_id, f, "generic_excel").duplicate_count == 2
