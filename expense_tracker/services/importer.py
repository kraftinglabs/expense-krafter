"""Two-step import: preview (parse + flag duplicates) then commit."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from ..db import get_session
from ..db.models import (Account, AccountType, CCStatement, Direction, ImportBatch, Transaction,
                         TxnKind)
from ..importers import ParsedStatement, ParsedTxn, get_parser
from .dedup import fingerprints


@dataclass
class PreviewRow:
    txn: ParsedTxn
    fingerprint: str
    duplicate: bool


@dataclass
class ImportPreview:
    account: Account
    path: Path
    filename: str
    parser_key: str
    file_sha256: str
    statement: ParsedStatement
    rows: list[PreviewRow] = field(default_factory=list)
    already_imported_file: bool = False

    @property
    def new_rows(self) -> list[PreviewRow]:
        return [r for r in self.rows if not r.duplicate]

    @property
    def duplicate_count(self) -> int:
        return len(self.rows) - len(self.new_rows)


# Bank debits that settle a credit card bill. Counting them as spend would
# double count the card purchases themselves.
CARD_BILL = re.compile(
    r"\b(cc|credit\s*card|card)\b.*\b(pay(ment)?|bill|dues?)\b|\b(pay(ment)?|bill)\b.*\bcredit\s*card\b"
    r"|\bcred\s*club\b|\bbilldesk\b.*\bcard\b", re.IGNORECASE)


def _default_kind(account: Account, direction: Direction, description: str = "") -> TxnKind:
    if account.type == AccountType.credit_card:
        # A credit on a card is a bill payment received or a refund.
        return TxnKind.expense if direction == Direction.debit else TxnKind.cc_payment
    if direction == Direction.debit and CARD_BILL.search(description):
        return TxnKind.cc_payment
    return TxnKind.expense if direction == Direction.debit else TxnKind.income


def preview_import(account_id: int, path: Path, parser_key: str | None = None,
                   filename: str | None = None, password: str | None = None) -> ImportPreview:
    with get_session() as s:
        account = s.get(Account, account_id)
        if account is None:
            raise ValueError(f"Unknown account {account_id}")
        parser_key = parser_key or account.parser_key
        statement = get_parser(parser_key).parse(path, password=password)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()

        fps = fingerprints(account_id, statement.transactions)
        existing = set(s.exec(
            select(Transaction.fingerprint).where(Transaction.account_id == account_id,
                                                  Transaction.fingerprint.in_(fps))
        ).all()) if fps else set()
        already = s.exec(select(ImportBatch).where(ImportBatch.account_id == account_id,
                                                   ImportBatch.file_sha256 == sha)).first() is not None

    return ImportPreview(
        account=account, path=path, filename=filename or path.name, parser_key=parser_key,
        file_sha256=sha, statement=statement, already_imported_file=already,
        rows=[PreviewRow(t, fp, fp in existing) for t, fp in zip(statement.transactions, fps)],
    )


def commit_import(preview: ImportPreview) -> ImportBatch:
    txns = preview.statement.transactions
    with get_session() as s:
        batch = ImportBatch(
            account_id=preview.account.id, filename=preview.filename, file_sha256=preview.file_sha256,
            parser_key=preview.parser_key,
            period_start=min((t.txn_date for t in txns), default=None),
            period_end=max((t.txn_date for t in txns), default=None),
            rows_total=len(preview.rows), rows_new=len(preview.new_rows),
            rows_duplicate=preview.duplicate_count,
        )
        s.add(batch)
        s.flush()

        stmt_id = None
        if (summary := preview.statement.card_summary) is not None:
            cc = CCStatement(account_id=preview.account.id, import_id=batch.id, **vars(summary))
            s.add(cc)
            s.flush()
            stmt_id = cc.id

        for row in preview.new_rows:
            t = row.txn
            s.add(Transaction(
                account_id=preview.account.id, import_id=batch.id, txn_date=t.txn_date,
                description=t.description, amount_minor=t.amount_minor, direction=t.direction,
                balance_minor=t.balance_minor, ref_no=t.ref_no, fingerprint=row.fingerprint,
                kind=_default_kind(preview.account, t.direction, t.description), cc_statement_id=stmt_id,
            ))
        s.commit()
        s.refresh(batch)
        return batch
