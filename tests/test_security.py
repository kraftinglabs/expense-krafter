import pandas as pd
import pypdf
import pytest
from msoffcrypto.format.ooxml import OOXMLFile

from expensekrafter.importers import PasswordRequired, get_parser
from expensekrafter.services.accounts import mask_account_number


def test_mask_keeps_only_first_five():
    assert mask_account_number("5010 0123-4567 89") == "50100XXXXXXXXX"
    assert mask_account_number("") == ""
    with pytest.raises(ValueError):
        mask_account_number("1234")
    with pytest.raises(ValueError):
        mask_account_number("12345#678")


@pytest.fixture
def encrypted_xlsx(tmp_path):
    plain = tmp_path / "plain.xlsx"
    pd.DataFrame([["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
                  ["01/01/2026", "UPI-SWIGGY", "250.00", None, "9,750.00"]]).to_excel(plain, header=False, index=False)
    enc = tmp_path / "enc.xlsx"
    with plain.open("rb") as src, enc.open("wb") as dst:
        OOXMLFile(src).encrypt("s3cret", dst)
    return enc


def test_encrypted_excel(encrypted_xlsx):
    parser = get_parser("generic_excel")
    with pytest.raises(PasswordRequired) as exc:
        parser.parse(encrypted_xlsx)
    assert not exc.value.wrong_password
    with pytest.raises(PasswordRequired) as exc:
        parser.parse(encrypted_xlsx, password="nope")
    assert exc.value.wrong_password
    assert len(parser.parse(encrypted_xlsx, password="s3cret").transactions) == 1


def test_encrypted_pdf(tmp_path):
    path = tmp_path / "enc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("s3cret")
    writer.write(path)

    parser = get_parser("generic_pdf")
    with pytest.raises(PasswordRequired) as exc:
        parser.parse(path)
    assert not exc.value.wrong_password
    with pytest.raises(PasswordRequired) as exc:
        parser.parse(path, password="nope")
    assert exc.value.wrong_password
    # Right password opens it (blank page, so just a "no tables" warning).
    assert parser.parse(path, password="s3cret").warnings


def test_data_dir_override_is_never_saved(tmp_path, monkeypatch):
    import json

    from expensekrafter import config

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_data_dir_override", None)
    s = config.Settings(data_dir="C:/real/data")
    config.override_data_dir(str(tmp_path / "dev"))
    assert s.data_path == tmp_path / "dev"
    s.theme = "dark"
    config.save_settings(s)
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["data_dir"] == "C:/real/data" and saved["theme"] == "dark"


def test_files_from_before_the_rename_are_adopted(tmp_path, monkeypatch):
    import json

    from expensekrafter import config

    old_cfg, old_data = tmp_path / "Roaming/ExpenseTracker", tmp_path / "Local/ExpenseTracker/data"
    new_cfg, new_data = tmp_path / "Roaming/ExpenseKrafter", tmp_path / "Local/ExpenseKrafter/data"
    for name, value in {"CONFIG_DIR": new_cfg, "SETTINGS_FILE": new_cfg / "settings.json",
                        "DEFAULT_DATA_DIR": new_data, "LEGACY_CONFIG_DIR": old_cfg,
                        "LEGACY_DATA_DIR": old_data}.items():
        monkeypatch.setattr(config, name, value)
    old_cfg.mkdir(parents=True)
    old_data.mkdir(parents=True)
    (old_cfg / "settings.json").write_text(json.dumps({"data_dir": str(old_data), "theme": "dark"}))
    (old_data / "expense_tracker.db").write_text("db")
    (old_data / "expense_tracker.lock").write_text("pc")
    new_cfg.mkdir(parents=True)
    (new_cfg / "app.log").write_text("")  # the packaged app opens its log first

    assert config.adopt_legacy_install()
    saved = json.loads((new_cfg / "settings.json").read_text())
    assert saved == {"data_dir": str(new_data), "theme": "dark"}
    assert (new_data / "expense_tracker.db").exists() and not (new_data / "expense_tracker.lock").exists()
    assert (old_data / "expense_tracker.db").exists()  # old copy kept as a fallback

    from expensekrafter.db.session import adopt_legacy_db
    s = config.Settings(data_dir=str(new_data))
    monkeypatch.setattr(config, "_data_dir_override", None)
    adopt_legacy_db(s)
    assert s.db_path.name == "expensekrafter.db" and s.db_path.read_text() == "db"
    assert not config.adopt_legacy_install()  # second start: nothing to do


def test_saved_passwords_move_to_the_new_name(tmp_path):
    import keyring

    from expensekrafter.db import init_db
    from expensekrafter.services import vault

    init_db(db_path=tmp_path / "v.db")
    keyring.set_password(vault.LEGACY_SERVICE, vault._user(3), "old-secret")
    assert vault.get_password(3) == "old-secret"
    assert keyring.get_password(vault.SERVICE, vault._user(3)) == "old-secret"
    assert keyring.get_password(vault.LEGACY_SERVICE, vault._user(3)) is None


def _linux_paths(home, env: dict, xdg_documents: str | None) -> dict:
    """Import config as it would load on Linux, in a separate process (the paths
    are fixed at import time)."""
    import json
    import os
    import subprocess
    import sys

    code = f"""
import json, pathlib, subprocess, sys
sys.platform = "linux"
pathlib.Path.home = classmethod(lambda cls: pathlib.Path({str(home)!r}))
def fake_run(cmd, **kw):
    if {xdg_documents!r} is None:
        raise FileNotFoundError(cmd[0])
    return subprocess.CompletedProcess(cmd, 0, {xdg_documents!r} + chr(10), "")
subprocess.run = fake_run
from expensekrafter import config
print(json.dumps({{"config": str(config.CONFIG_DIR), "data": str(config.DEFAULT_DATA_DIR),
                  "legacy_data": str(config.LEGACY_DATA_DIR)}}))
"""
    clean = {k: v for k, v in os.environ.items() if k not in ("XDG_CONFIG_HOME",)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**clean, **env},
                         check=True)
    return {k: v.replace("\\", "/") for k, v in json.loads(out.stdout).items()}


def test_linux_keeps_data_in_documents(tmp_path):
    home = tmp_path.as_posix()
    paths = _linux_paths(tmp_path, {}, xdg_documents=None)  # no xdg-user-dir: ~/Documents
    assert paths["data"] == f"{home}/Documents/ExpenseKrafter"
    assert paths["config"] == f"{home}/.config/ExpenseKrafter"
    assert paths["legacy_data"] == f"{home}/.local/share/ExpenseTracker/data"

    # A localised / moved Documents folder and a custom XDG config dir are respected.
    paths = _linux_paths(tmp_path, {"XDG_CONFIG_HOME": f"{home}/cfg"}, xdg_documents=f"{home}/Dokumente")
    assert paths["data"] == f"{home}/Dokumente/ExpenseKrafter"
    assert paths["config"] == f"{home}/cfg/ExpenseKrafter"


def test_password_not_saved_without_a_keyring(tmp_path, monkeypatch):
    import keyring
    from keyring.errors import KeyringError

    from expensekrafter.db import init_db
    from expensekrafter.services import vault

    init_db(db_path=tmp_path / "k.db")

    def unavailable(*args):
        raise KeyringError("no Secret Service")

    monkeypatch.setattr(keyring, "set_password", unavailable)
    assert vault.set_password(1, "secret") is False


def test_web_mode_needs_a_password_beyond_localhost(monkeypatch):
    import pytest as _pytest

    from expensekrafter import __main__ as entry
    from expensekrafter.ui import auth

    assert entry.parse_args(["--browser"]).web  # dev shortcut = --web --no-open
    monkeypatch.delenv(auth.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(entry.ui, "run", lambda **kw: _pytest.fail("must not start"))
    monkeypatch.setattr("sys.argv", ["expensekrafter", "--web", "--host", "0.0.0.0", "--data-dir", "x"])
    with _pytest.raises(SystemExit, match="without a password"):
        entry.main()


def test_login_only_redirects_within_the_app():
    from expensekrafter.ui.auth import safe_next

    assert safe_next("/transactions") == "/transactions"
    for bad in ("//evil.example", "/\\evil.example","https://evil.example", "evil"):
        assert safe_next(bad) == "/"
