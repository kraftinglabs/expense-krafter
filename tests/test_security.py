import pandas as pd
import pypdf
import pytest
from msoffcrypto.format.ooxml import OOXMLFile

from expense_tracker.importers import PasswordRequired, get_parser
from expense_tracker.services.accounts import mask_account_number


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

    from expense_tracker import config

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
