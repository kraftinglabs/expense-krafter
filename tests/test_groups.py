from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, ExpenseGroup, Transaction, TxnKind
from expensekrafter.services import analytics, groups, splits
from expensekrafter.services.groups import GroupError


@pytest.fixture
def ids(tmp_path):
    """Made-up rows: a hotel and a cab on the bank account, and a card with no statement yet."""
    init_db(db_path=tmp_path / "g.db")
    with get_session() as s:
        bank = Account(name="Bank", type=AccountType.bank)
        card = Account(name="Card", type=AccountType.credit_card)
        s.add_all([bank, card])
        s.flush()
        txns = [Transaction(account_id=bank.id, txn_date=date(2026, 11, d), description=desc, amount_minor=r * 100,
                            direction=Direction.debit, kind=TxnKind.expense, fingerprint=f"f{d}")
                for d, desc, r in [(3, "POS HOTEL", 6000), (4, "UPI/DR/2/CABS/c@y", 2000)]]
        s.add_all(txns)
        s.commit()
        return bank.id, card.id, [t.id for t in txns]


def test_groups_are_made_first_and_filled_later(ids):
    bank, card, (hotel, cab) = ids
    gid = groups.create_group("Vietnam trip, Nov 2026")
    with pytest.raises(GroupError, match="already"):
        groups.create_group(" vietnam trip,  nov 2026 ")
    [g] = groups.groups()
    assert (g.name, g.count, g.total_minor) == ("Vietnam trip, Nov 2026", 0, 0)  # empty, and kept

    assert groups.add_to_group([hotel], gid) == 1
    assert groups.add_to_group([hotel, cab], gid) == 1  # the hotel is already in
    expected = groups.add_expected(gid, card, date(2026, 11, 5), 150000, "Dinner in Hanoi")
    [g] = groups.groups()
    assert (g.count, g.waiting, g.total_minor, g.first_date) == (2, 1, 950000, date(2026, 11, 3))
    assert splits.pending_items()[0].txn_id == expected

    groups.remove_from_group([cab])
    [g] = groups.groups()
    assert [i.txn_id for i in g.items] == [hotel, expected]
    groups.remove_from_group([hotel])
    assert groups.groups()[0].items == [g.items[1]]  # emptying real rows keeps the group


def test_expected_item_joins_the_group_when_imported(ids):
    bank, card, (hotel, cab) = ids
    gid = groups.create_group("Trip")
    groups.add_to_group([hotel], gid)
    expected = groups.add_expected(gid, card, date(2026, 11, 5), 150000, "Dinner in Hanoi")
    assert analytics.spend_frame()["amount"].sum() == 8000  # not counted until it's real

    with get_session() as s:
        real = Transaction(account_id=card, txn_date=date(2026, 11, 6), description="POS PHO 24", amount_minor=150000,
                           direction=Direction.debit, kind=TxnKind.expense, fingerprint="r")
        s.add(real)
        s.commit()
        real_id = real.id
    [(item, found)] = splits.pending_matches({real_id})
    assert item.txn_id == expected and item.people == [] and found[0][0].id == real_id
    splits.attach_pending(expected, real_id)
    [g] = groups.groups()
    assert {i.txn_id for i in g.items} == {hotel, real_id} and g.waiting == 0


def test_whole_trip_reimbursed_and_delete_rules(ids):
    bank, card, (hotel, cab) = ids
    gid = groups.create_group("Trip")
    groups.add_to_group([hotel, cab], gid)
    splits.save_split(None, {"Company": 800000}, group_id=gid)
    assert groups.groups()[0].split.owed_minor == 800000
    with pytest.raises(GroupError, match="split"):
        groups.delete_group(gid)
    other = groups.create_group("Other")
    with pytest.raises(GroupError, match="would empty"):
        groups.add_to_group([hotel, cab], other)
    splits.delete_split(None, group_id=gid)
    groups.delete_group(gid)
    with get_session() as s:
        assert s.get(ExpenseGroup, gid) is None and s.get(Transaction, hotel).group_id is None
