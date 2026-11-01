from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, Label, Transaction, TxnKind
from expensekrafter.services import analytics, labels, ledger


@pytest.fixture
def ids(tmp_path):
    """Made-up rows: an SIP, a one-time investment and lunch, all in October."""
    init_db(db_path=tmp_path / "g.db")
    invest = labels.create_label("Investments")
    sip = labels.create_label("SIP", parent_id=invest.id)
    lump = labels.create_label("One-time investment", parent_id=invest.id)
    food = labels.create_label("Food")
    with get_session() as s:
        bank = Account(name="Bank", type=AccountType.bank)
        s.add(bank)
        s.flush()
        for i, (lab, rupees) in enumerate([(sip, 5000), (lump, 20000), (food, 300)]):
            s.add(Transaction(account_id=bank.id, txn_date=date(2026, 10, 5 + i), description=f"PAY {i}",
                              amount_minor=rupees * 100, direction=Direction.debit, kind=TxnKind.expense,
                              label_id=lab.id, fingerprint=f"f{i}"))
        s.commit()
    return invest.id, sip.id, lump.id, food.id


def test_grouped_labels_count_as_the_group(ids):
    invest, sip, lump, food = ids
    df = analytics.spend_frame(analytics.period_for("2026-10"))
    assert df.groupby("label")["amount"].sum().to_dict() == {"Investments": 25000.0, "Food": 300.0}
    assert set(df.loc[df["label"] == "Investments", "sublabel"]) == {"SIP", "One-time investment"}
    with get_session() as s:
        group_color = s.get(Label, invest).color
    assert set(df.loc[df["label"] == "Investments", "color"]) == {group_color}
    # Filtering transactions by the group takes the labels inside it.
    rows = ledger.filtered_rows(ledger.TxnFilter(label_ids={invest}))
    assert {r.label_id for r in rows} == {sip, lump}


def test_groups_are_one_level_deep(ids):
    invest, sip, lump, food = ids
    with pytest.raises(ValueError):  # SIP is inside a group, so it can't be one
        labels.update_label(food, name="Food", color="#000", recurring=False, parent_id=sip)
    with pytest.raises(ValueError):  # Investments holds labels, so it can't go into a group
        labels.update_label(invest, name="Investments", color="#000", recurring=False, parent_id=food)
    with pytest.raises(ValueError):
        labels.update_label(food, name="Food", color="#000", recurring=False, parent_id=food)
    labels.update_label(sip, name="SIP", color="#000", recurring=True, parent_id=None)  # ungroup
    assert labels.with_children({invest}) == {invest, lump}


def test_deleting_a_group_ungroups_its_labels(ids):
    invest, sip, lump, food = ids
    labels.delete_label(invest)
    with get_session() as s:
        assert s.get(Label, sip).parent_id is None


def test_set_parent_moves_a_label_in_and_out(ids):
    invest, sip, lump, food = ids
    assert labels.set_parent(food, invest).parent_id == invest
    assert labels.set_parent(food, None).parent_id is None
    with pytest.raises(ValueError):  # a group can't be dragged into another group
        labels.set_parent(invest, food)
