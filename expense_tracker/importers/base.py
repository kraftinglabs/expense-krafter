"""Parser interface and registry.

Each bank/card statement format gets a parser that turns a file into a
ParsedStatement. Parsers only extract data; dedup and saving happen in
services.importer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, ClassVar

from ..db.models import AccountType, Direction


@dataclass
class ParsedTxn:
    txn_date: date
    description: str
    amount_minor: int
    direction: Direction
    balance_minor: int | None = None
    ref_no: str = ""


@dataclass
class CardSummary:
    statement_date: date
    due_date: date
    total_due_minor: int
    min_due_minor: int = 0
    period_start: date | None = None
    period_end: date | None = None


@dataclass
class ParsedStatement:
    transactions: list[ParsedTxn] = field(default_factory=list)
    card_summary: CardSummary | None = None
    warnings: list[str] = field(default_factory=list)


class PasswordRequired(Exception):
    """The file is encrypted and no password, or the wrong one, was given."""

    def __init__(self, wrong_password: bool):
        self.wrong_password = wrong_password
        super().__init__("Wrong password" if wrong_password else "File is password protected")


class StatementParser:
    key: ClassVar[str]
    display_name: ClassVar[str]
    account_type: ClassVar[AccountType | None] = None  # None = works for both
    extensions: ClassVar[tuple[str, ...]]

    def parse(self, path: Path, password: str | None = None) -> ParsedStatement:
        raise NotImplementedError


PARSERS: dict[str, type[StatementParser]] = {}


def register(cls: type[StatementParser]) -> type[StatementParser]:
    PARSERS[cls.key] = cls
    return cls


def get_parser(key: str) -> StatementParser:
    return PARSERS[key]()


def parser_options(account_type: AccountType | None = None) -> dict[str, str]:
    return {
        k: p.display_name
        for k, p in PARSERS.items()
        if account_type is None or p.account_type in (None, account_type)
    }


# Helper type for column-sniffing parsers
CellParser = Callable[[object], object]
