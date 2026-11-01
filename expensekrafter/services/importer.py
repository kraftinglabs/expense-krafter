"""Two-step import: preview (parse + flag duplicates) then commit."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import func
from sqlmodel import select

from ..db import get_session
from ..db.models import (Account, AccountType, CCStatement, Direction, ImportBatch, SkippedRow, Transaction,
                         TxnKind)
from ..importers import ParsedStatement, ParsedTxn, get_parser
from . import narration
from .dedup import fingerprints


@dataclass
class PreviewRow:
    txn: ParsedTxn
    fingerprint: str
    duplicate: bool              # already imported: never imported again
    skipped_before: bool = False  # you unticked it in an earlier import: starts unticked


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


def _plain_kind(account: Account, direction: Direction) -> TxnKind:
    """Type from the direction alone, before any description rule."""
    if account.type == AccountType.credit_card:
        # A credit on a card is a bill payment received or a refund.
        return TxnKind.expense if direction == Direction.debit else TxnKind.cc_payment
    return TxnKind.expense if direction == Direction.debit else TxnKind.income


def default_kind(account: Account, direction: Direction, description: str = "") -> TxnKind:
    # Description rules can set the type of bank rows, e.g. card bill payments
    # (a transfer, not spend: counting them would double count the purchases).
    if account.type == AccountType.bank and (kind := narration.parse(description).kind):
        if kind != TxnKind.cc_payment.value or direction == Direction.debit:
            return TxnKind(kind)
    return _plain_kind(account, direction)


@dataclass
class KindChange:
    txn_id: int
    txn_date: date
    account: str
    description: str
    amount_minor: int
    direction: Direction
    old: TxnKind
    new: TxnKind


def reapply_rule_types(apply: bool = False) -> list[KindChange]:
    """Re-run the description rules' types over existing transactions.

    Only rows still carrying the plain default type are touched: anything set
    by hand (kind_manual), by linking a card bill, or otherwise changed from
    the default is left alone. With apply=False nothing is written.
    """
    changes = []
    with get_session() as s:
        rows = s.exec(select(Transaction, Account).join(Account, Account.id == Transaction.account_id)
                      .where(Transaction.kind_manual == False)).all()  # noqa: E712
        for t, acc in rows:
            if t.kind != _plain_kind(acc, t.direction):
                continue
            new = default_kind(acc, t.direction, t.description)
            if new != t.kind:
                changes.append(KindChange(t.id, t.txn_date, acc.name, t.description, t.amount_minor,
                                          t.direction, t.kind, new))
                if apply:
                    t.kind = new
                    s.add(t)
        if apply:
            s.commit()
    return changes


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
        skipped = set(s.exec(
            select(SkippedRow.fingerprint).where(SkippedRow.account_id == account_id,
                                                 SkippedRow.fingerprint.in_(fps))
        ).all()) if fps else set()

    return ImportPreview(
        account=account, path=path, filename=filename or path.name, parser_key=parser_key,
        file_sha256=sha, statement=statement, already_imported_file=already,
        rows=[PreviewRow(t, fp, fp in existing, fp in skipped) for t, fp in zip(statement.transactions, fps)],
    )


def commit_import(preview: ImportPreview, selected: set[str] | None = None) -> ImportBatch:
    """Import the new rows whose fingerprint is in `selected` (None = every new
    row not skipped before). New rows left out are remembered as skipped, so
    they start unticked next time; ticking a skipped row imports it."""
    txns = preview.statement.transactions
    new_rows = preview.new_rows
    if selected is None:
        selected = {r.fingerprint for r in new_rows if not r.skipped_before}
    chosen = [r for r in new_rows if r.fingerprint in selected]
    left_out = [r for r in new_rows if r.fingerprint not in selected]
    account_id = preview.account.id

    with get_session() as s:
        batch = ImportBatch(
            account_id=account_id, filename=preview.filename, file_sha256=preview.file_sha256,
            parser_key=preview.parser_key,
            period_start=min((t.txn_date for t in txns), default=None),
            period_end=max((t.txn_date for t in txns), default=None),
            rows_total=len(preview.rows), rows_new=len(chosen),
            rows_duplicate=preview.duplicate_count,
        )
        s.add(batch)
        s.flush()

        stmt_id = None
        if (summary := preview.statement.card_summary) is not None:
            # Re-importing the same statement must not create a second bill.
            cc = s.exec(select(CCStatement).where(CCStatement.account_id == account_id,
                                                  CCStatement.statement_date == summary.statement_date)).first()
            if cc is None:
                cc = CCStatement(account_id=account_id, import_id=batch.id, **vars(summary))
                s.add(cc)
                s.flush()
            stmt_id = cc.id

        for row in left_out:
            if not row.skipped_before:
                s.add(SkippedRow(account_id=account_id, fingerprint=row.fingerprint))
        for row in chosen:
            if row.skipped_before:  # changed your mind: forget the skip
                for old in s.exec(select(SkippedRow).where(SkippedRow.account_id == account_id,
                                                           SkippedRow.fingerprint == row.fingerprint)):
                    s.delete(old)

        for row in chosen:
            t = row.txn
            s.add(Transaction(
                account_id=preview.account.id, import_id=batch.id, txn_date=t.txn_date,
                description=t.description, amount_minor=t.amount_minor, direction=t.direction,
                balance_minor=t.balance_minor, ref_no=t.ref_no, fingerprint=row.fingerprint,
                kind=default_kind(preview.account, t.direction, t.description), cc_statement_id=stmt_id,
            ))
        s.commit()
        s.refresh(batch)
        return batch


@dataclass
class HistoryRow:
    batch: ImportBatch
    account: str


def import_history() -> list[HistoryRow]:
    """Every import, newest first."""
    with get_session() as s:
        rows = s.exec(select(ImportBatch, Account.name).join(Account, Account.id == ImportBatch.account_id)
                      .order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())).all()
    return [HistoryRow(b, acc) for b, acc in rows]


@dataclass
class AccountStatus:
    """Where an account's imported data stops: its latest transaction and import."""
    account: Account
    txn_count: int
    last_txn: Transaction | None
    last_import: ImportBatch | None


def account_statuses() -> list[AccountStatus]:
    """One per active account, in the usual account order."""
    with get_session() as s:
        accounts = s.exec(select(Account).where(Account.active == True)  # noqa: E712
                          .order_by(Account.type, Account.name)).all()
        counts = dict(s.exec(select(Transaction.account_id, func.count())
                             .group_by(Transaction.account_id)).all())
        out = []
        for acc in accounts:
            last_txn = s.exec(select(Transaction).where(Transaction.account_id == acc.id)
                              .order_by(Transaction.txn_date.desc(), Transaction.id.desc())).first()
            last_import = s.exec(select(ImportBatch).where(ImportBatch.account_id == acc.id)
                                 .order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())).first()
            out.append(AccountStatus(acc, counts.get(acc.id, 0), last_txn, last_import))
    return out
