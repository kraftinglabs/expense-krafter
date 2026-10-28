import random
from datetime import date

import pytest

from expensekrafter.db import get_session, init_db
from expensekrafter.db.models import Account, AccountType, Direction, IntervalUnit, Label, Transaction
from expensekrafter.services import labels


@pytest.fixture(autouse=True)
def db(tmp_path):
    init_db(db_path=tmp_path / "test.db")


def test_new_labels_get_unused_palette_colours():
    rng = random.Random(1)
    colours = {labels.create_label(f"L{i}", color=labels.pick_color(rng)).color for i in range(5)}
    assert len(colours) == 5 and colours <= set(labels.PALETTE)
    assert labels.create_label("Auto").color in labels.PALETTE  # colour assigned when none given


def test_edit_changes_name_colour_and_recurrence():
    lab = labels.create_label("Subs")
    lab = labels.update_label(lab.id, name="Subscriptions", color="#123456", recurring=True,
                              every=6, unit=IntervalUnit.month)
    assert (lab.name, lab.color, lab.is_recurring, lab.every, lab.unit) == ("Subscriptions", "#123456", True,
                                                                           6, IntervalUnit.month)
    lab = labels.update_label(lab.id, name="Subscriptions", color="#123456", recurring=False,
                              every=6, unit=IntervalUnit.month)
    assert (lab.every, lab.unit) == (None, None)  # not recurring -> no interval
    with pytest.raises(ValueError):
        labels.update_label(lab.id, name="Subscriptions", color="#123456", recurring=True, every=0)


def test_interval_names_and_yearly_count():
    assert labels.interval_name(6, IntervalUnit.month) == "Half-yearly"
    assert labels.interval_name(1, IntervalUnit.week) == "Weekly"
    assert labels.interval_name(2, IntervalUnit.week) == "Every 2 weeks"
    assert labels.interval_name(18, IntervalUnit.month) == "Every 18 months"
    assert labels.times_per_year(6, IntervalUnit.month) == 2
    assert labels.times_per_year(2, IntervalUnit.week) == 26
    assert labels.times_per_year(2, IntervalUnit.year) == 0.5


def test_names_must_be_unique_ignoring_case():
    labels.create_label("Food")
    with pytest.raises(ValueError):
        labels.create_label("food")
    other = labels.create_label("Fuel")
    with pytest.raises(ValueError):
        labels.update_label(other.id, name="FOOD", color="#000000", recurring=False)


def test_used_labels_cannot_be_deleted():
    used, unused = labels.create_label("Used"), labels.create_label("Unused")
    with get_session() as s:
        acc = Account(name="Bank", type=AccountType.bank)
        s.add(acc)
        s.flush()
        s.add(Transaction(account_id=acc.id, txn_date=date(2026, 1, 1), description="x", amount_minor=1,
                          direction=Direction.debit, fingerprint="f", label_id=used.id))
        s.commit()
    assert labels.usage_count(used.id) == 1
    with pytest.raises(ValueError):
        labels.delete_label(used.id)
    labels.delete_label(unused.id)
    with get_session() as s:
        assert s.get(Label, unused.id) is None and s.get(Label, used.id) is not None
