from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Label, Transaction, TxnKind
from expensekrafter.services import analytics, labels, payroll
from expensekrafter.services.payroll import PayrollError


@pytest.fixture
def ids(tmp_path):
    """Made-up rows: a ₹1,00,000 salary and ₹4,000 of food in October."""
    init_db(db_path=tmp_path / "p.db")
    with get_session() as s:
        bank = Account(name="Bank", type=AccountType.bank)
        food = Label(name="Food & Dining", color="#e45756")
        s.add_all([bank, food])
        s.flush()
        s.add_all([
            Transaction(account_id=bank.id, txn_date=date(2026, 10, 31), description="SALARY ACME", fingerprint="a",
                        amount_minor=10_000_000, direction=Direction.credit, kind=TxnKind.income),
            Transaction(account_id=bank.id, txn_date=date(2026, 10, 9), description="UPI/DR/1/HUNGERBOX/h@y",
                        fingerprint="b", amount_minor=400_000, direction=Direction.debit, kind=TxnKind.expense,
                        label_id=food.id),
        ])
        s.commit()
        return bank.id, food.id


def test_salary_reimbursement_comes_off_spend_and_income(ids):
    bank, food = ids
    october = analytics.period_for("2026-10")
    payroll.save(None, date(2026, 10, 31), 275_000, food, bank, " HungerBox ")
    spend, income = analytics.spend_frame(october), analytics.income_frame(october)
    assert spend.groupby("label")["amount"].sum().to_dict() == {"Food & Dining": 1250.0}
    assert income["amount"].sum() == 97250.0
    assert income["amount"].sum() - spend["amount"].sum() == 96000.0  # what you kept doesn't change
    assert analytics.spend_frame(analytics.period_for("2026-11")).empty
    assert analytics.spend_frame(october, account_id=bank + 1).empty  # other accounts' filter skips it
    assert payroll.entries()[0].note == "HungerBox"
    assert payroll.salary_account() == bank


def test_salary_reimbursement_edit_delete_and_checks(ids):
    bank, food = ids
    entry = payroll.save(None, date(2026, 10, 31), 100_000, food, None)
    payroll.save(entry, date(2026, 10, 30), 120_000, food, None)
    assert [(e.paid_on, e.amount_minor, e.label) for e in payroll.entries()] == [(date(2026, 10, 30), 120_000,
                                                                                  "Food & Dining")]
    with pytest.raises(ValueError):  # a label in use can't be deleted
        labels.delete_label(food)
    with pytest.raises(PayrollError):
        payroll.save(None, date(2026, 10, 30), 0, food, None)
    with pytest.raises(PayrollError):
        payroll.save(None, date(2026, 10, 30), 100, None, None)
    payroll.delete(entry)
    assert payroll.entries() == []
