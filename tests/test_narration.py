"""Description rules. All examples are made up but follow the exact layout of
SBI savings exports, including the mid-word line wraps."""

from expensekrafter.services import narration
from expensekrafter.services.analytics import merchant_name
from expensekrafter.services.narration import load_rules, parse

TRAILER = "   0097691162095 AT 12345 MG ROAD BRANCH"


def test_sbi_upi_debit_rejoins_wrapped_upi_id():
    n = parse("WDL TFR   UPI/DR/123456789012/RAVI KUM/HDFC/ravikumar.9\n 8/NO R" + TRAILER)
    assert (n.method, n.title, n.ref) == ("UPI", "Ravi Kum", "123456789012")
    assert n.details == ["ravikumar.98", "HDFC"]  # "NO R" (no remarks) is dropped


def test_sbi_upi_credit_lowercase_bank_code():
    n = parse("DEP TFR   UPI/CR/987654321098/ACME STO/utib/acme.store\n s/Paym" + TRAILER)
    assert (n.title, n.details) == ("Acme Sto", ["acme.stores", "Axis"])


def test_sbi_neft_unwraps_name():
    n = parse("DEP TFR   NEFT*HDFC0000123*N123456789*ACME TECHNOL\n OGIES" + TRAILER)
    assert (n.method, n.title, n.details, n.ref) == ("NEFT", "Acme Technologies", ["HDFC"], "N123456789")


def test_sbi_card_bill_is_titled_and_typed():
    n = parse("WDL TFR   INB SBICARD FOR BILLDESK      SBICARD Paym\n ents" + TRAILER)
    assert (n.method, n.title, n.kind) == ("Net banking", "SBI Card bill payment", "cc_payment")


def test_unmatched_shows_cleaned_plain_text():
    n = parse("WDL TFR   SOMETHING NEW   FORMAT\n X" + TRAILER)
    assert not n.parsed and n.display == "SOMETHING NEW FORMATX"


def test_merchant_name_uses_rules():
    assert merchant_name("WDL TFR   UPI/DR/123456789012/RAVI KUM/HDFC/ravikumar.9\n 8/NO R" + TRAILER) == "Ravi Kum"
    assert merchant_name("UPI-SWIGGY-1234567@ybl") == "Swiggy"
    assert merchant_name("NETFLIX") == "Netflix"  # no rule: rough clean-up


def test_user_rules_file_wins_and_reloads(tmp_path, monkeypatch):
    user = tmp_path / "narration_rules.toml"
    monkeypatch.setattr(narration, "USER_RULES", user)
    assert narration.rules_path() == narration.DEFAULT_RULES

    user.write_text("""
[[rule]]
name = "Rent"
pattern = 'LANDLORD'
title = "Rent to landlord"
method = "Rent"
""", encoding="utf-8")
    assert parse("NEFT-LANDLORD 99").title == "Rent to landlord"

    user.write_text("[[rule]]\npattern = '(broken'\ntitle = 'x'\n", encoding="utf-8")
    import os
    os.utime(user, (user.stat().st_atime, user.stat().st_mtime + 5))  # make sure mtime changes
    assert parse("UPI-SWIGGY-1@ybl").title == "Swiggy"  # broken file -> defaults
    assert narration.rules_error()


def test_default_rules_file_is_valid():
    rules = load_rules(narration.DEFAULT_RULES)
    assert rules.rules and rules.cleanup and "SBIN" in rules.banks


def test_niyo_descriptions():
    # Made-up rows in Niyo's statement format.
    n = parse("POS VS/612345678901/12:19:55/Grab* A-1AB/XYZ123")
    assert (n.title, n.method, n.details, n.ref) == ("Grab", "Card", ["A-1AB", "12:19:55"], "612345678901")
    assert parse("POS VS/612345678902/09:02:32/HOTEL SUNRISE/").title == "Hotel Sunrise"
    assert parse("POS VS/612345678903/20:29:21/NET*CORNER CAFE").details == ["via Net", "20:29:21"]
    rev = parse("POS VS REV/612345678901/12:19:55/Grab* /")
    assert (rev.title, rev.kind) == ("Grab (reversed)", "refund")
    atm = parse("VISACWD ATM 1234/612345678904/17:37:45/MAIN STREET")
    assert (atm.title, atm.method, atm.details) == ("Cash withdrawal", "ATM", ["Main Street", "17:37:45"])
    assert parse("DT:14-10-2026 INTERNATIONAL ATM CASH WTHDRWL").title == "International ATM fee"
    assert parse("CGST FOR DT:14-10-2026 INTERNATION").title == "CGST on bank fee"
    upi = parse("UPI:REC:012345678901/JANE DOE/Some Bank")
    assert (upi.title, upi.method, upi.details) == ("Jane Doe", "UPI", ["Some Bank"])
    neft = parse("MB NEFT/DCBLN0000000000000001/HDFC/JOHN SMITH")
    assert (neft.title, neft.method, neft.details) == ("John Smith", "NEFT", ["HDFC"])


def test_niyo_cut_merchant_names_and_footer():
    # Niyo keeps 11 characters of the merchant, overwrites the 12th with '/'.
    assert parse("POS VS/612345678905/10:11:57/CORNER CAFE/S AND BAKES").title == "Corner Cafe·S And Bakes"
    assert parse("POS VS/612345678906/10:25:24/SHOP NO 57/").title == "Shop No 57"  # name ended at 11
    assert parse("POS VS/612345678907/10:11:57/TEA (Old To/n Road)").title == "Tea (Old To·N Road)"
    footer = "MB NEFT/DCBLN0000000000000002/HDFC/JOHN SMITH Closing Balance ACCOUNT SCHEME CODE NA Customer Name X"
    assert parse(footer).title == "John Smith"


def test_personal_aliases_win_and_reload(tmp_path, monkeypatch):
    from expensekrafter.services import narration

    aliases = tmp_path / "my_rules.toml"
    monkeypatch.setattr(narration, "USER_EXTRA", aliases)
    raw = "POS VS/612345678905/10:11:57/CORNER CAFE/S AND BAKES"
    assert parse(raw).title == "Corner Cafe·S And Bakes"
    aliases.write_text("[[alias]]\npattern = '^corner cafe·s'\nname = \"Corner Cafe's and Bakes\"\n",
                       encoding="utf-8")
    assert parse(raw).title == "Corner Cafe's and Bakes"
    assert narration.rules_error() is None


def test_personal_rules_set_the_type_first(tmp_path, monkeypatch):
    from expensekrafter.services import narration

    mine = tmp_path / "my_rules.toml"
    monkeypatch.setattr(narration, "USER_EXTRA", mine)
    mine.write_text("[[rule]]\nname = 'me'\npattern = '\\bJOHN\\s+Q\\s+SMITH\\b'\nkind = 'transfer'\n",
                    encoding="utf-8")
    own = parse("MB NEFT/DCBLN0000000000000003/HDFC/JOHN Q SMITH")
    assert (own.title, own.kind) == ("John Q Smith", "transfer")  # title still from the shared rule
    assert parse("MB NEFT/DCBLN0000000000000004/HDFC/JOHN SMITH").kind is None
