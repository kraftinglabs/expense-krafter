from expense_tracker.db import init_db
from expense_tracker.services import vault


def test_passwords_do_not_leak_between_databases(tmp_path):
    init_db(db_path=tmp_path / "a.db")
    vault.set_password(1, "secret-a")
    assert vault.get_password(1) == "secret-a"

    init_db(db_path=tmp_path / "b.db")  # another DB whose account also has id 1
    assert vault.get_password(1) is None
    assert not vault.has_password(1)

    init_db(db_path=tmp_path / "a.db")  # reopening the first DB finds it again
    assert vault.get_password(1) == "secret-a"
    vault.forget_password(1)
    assert vault.get_password(1) is None
