from __future__ import annotations

import re

from sqlalchemy import func
from sqlmodel import select

from ..db import get_session
from ..db.models import Account, CCStatement, ImportBatch, Transaction
from . import vault

VISIBLE_PREFIX = 5


def mask_account_number(raw: str) -> str:
    """'5010 0123-4567 89' -> '50100XXXXXXXXX'. Separators are dropped; only the
    first 5 characters survive."""
    number = re.sub(r"[\s\-./]", "", raw or "").upper()
    if not number:
        return ""
    if not number.isalnum():
        raise ValueError("Account number can only contain letters and digits")
    if len(number) <= VISIBLE_PREFIX:
        raise ValueError(f"Account number must be longer than {VISIBLE_PREFIX} characters")
    return number[:VISIBLE_PREFIX] + "X" * (len(number) - VISIBLE_PREFIX)


def save_account(account: Account, full_number: str | None = None) -> Account:
    """Insert or update. full_number, when given, replaces the stored masked
    number; None or blank keeps the current one."""
    if full_number and full_number.strip():
        account.number_masked = mask_account_number(full_number)
    with get_session() as s:
        account = s.merge(account)
        s.commit()
        s.refresh(account)
        return account


def usage_count(account_id: int) -> int:
    """Rows that belong to the account (transactions + card statements)."""
    with get_session() as s:
        txns = s.exec(select(func.count()).select_from(Transaction)
                      .where(Transaction.account_id == account_id)).one()
        stmts = s.exec(select(func.count()).select_from(CCStatement)
                       .where(CCStatement.account_id == account_id)).one()
    return txns + stmts


def delete_account(account_id: int) -> None:
    """Delete an account that has no data. Accounts with transactions or
    statements should be marked inactive instead."""
    if (n := usage_count(account_id)):
        raise ValueError(f"Account has {n} transactions/statements; mark it inactive instead")
    with get_session() as s:
        for batch in s.exec(select(ImportBatch).where(ImportBatch.account_id == account_id)):
            s.delete(batch)
        s.delete(s.get(Account, account_id))
        s.commit()
    vault.forget_password(account_id)
