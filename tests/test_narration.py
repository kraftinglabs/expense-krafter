"""Description rules. All examples are made up but follow the exact layout of
SBI savings exports, including the mid-word line wraps."""

from expense_tracker.services import narration
from expense_tracker.services.analytics import merchant_name
from expense_tracker.services.narration import load_rules, parse

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
