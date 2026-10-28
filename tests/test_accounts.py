from datetime import date

import pytest

from expense_tracker.db import get_session, init_db
from expense_tracker.db.models import Account, AccountType, Direction, Transaction
from expense_tracker.services.accounts import delete_account, save_account


@pytest.fixture(autouse=True)
def db(tmp_path):
    init_db(db_path=tmp_path / "test.db")


def test_edit_keeps_masked_number_unless_new_one_given():
    acc = save_account(Account(name="SBI", type=AccountType.bank), "3012345678901")
    assert acc.number_masked == "30123XXXXXXXX"

    acc.name = "SBI Salary"
    acc = save_account(acc, "")
    assert (acc.name, acc.number_masked) == ("SBI Salary", "30123XXXXXXXX")

    acc = save_account(acc, "9988 7766 55")
    assert acc.number_masked == "99887XXXXX"
    with get_session() as s:
        assert s.get(Account, acc.id).name == "SBI Salary"


def test_delete_refuses_accounts_with_transactions():
    used = save_account(Account(name="Used", type=AccountType.bank))
    empty = save_account(Account(name="Empty", type=AccountType.bank))
    with get_session() as s:
        s.add(Transaction(account_id=used.id, txn_date=date(2026, 1, 1), description="x", amount_minor=100,
                          direction=Direction.debit, fingerprint="f"))
        s.commit()

    with pytest.raises(ValueError):
        delete_account(used.id)
    delete_account(empty.id)
    with get_session() as s:
        assert s.get(Account, empty.id) is None
        assert s.get(Account, used.id) is not None
