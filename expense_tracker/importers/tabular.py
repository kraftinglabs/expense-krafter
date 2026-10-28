"""Generic parsers that sniff common column names.

They work for many Excel/CSV exports and text-based PDF tables out of the box,
and are the fallback until a bank-specific parser is written from a sample.
"""

from __future__ import annotations

import io
import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from ..db.models import Direction
from .base import ParsedStatement, ParsedTxn, PasswordRequired, StatementParser, register

DATE_COLS = ("txn date", "transaction date", "date", "value date", "tran date")
DESC_COLS = ("narration", "description", "particulars", "details", "remarks", "transaction details")
DEBIT_COLS = ("withdrawal amt.", "withdrawal amount", "withdrawal", "debit amount", "debit", "dr", "withdrawals")
CREDIT_COLS = ("deposit amt.", "deposit amount", "deposit", "credit amount", "credit", "cr", "deposits")
AMOUNT_COLS = ("amount", "transaction amount", "amount (inr)", "amt")
BALANCE_COLS = ("closing balance", "balance", "available balance", "running balance")
REF_COLS = ("chq./ref.no.", "ref no./cheque no.", "ref no", "reference no", "cheque no", "chq no", "utr", "ref")
DRCR_COLS = ("dr/cr", "cr/dr", "type", "debit/credit")

DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y",
                "%d %b %y", "%d-%b-%y", "%Y-%m-%d", "%d.%m.%Y")


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def parse_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(value: object) -> int | None:
    """'1,23,456.78' / '(500.00)' / '500 Dr' -> minor units (always >= 0)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = re.sub(r"(?i)\b(dr|cr|inr|rs\.?)\b|[₹,\s()]", "", str(value))
    text = text.lstrip("+-")
    if not text:
        return None
    try:
        return int((Decimal(text) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        return None


def _find(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    for cand in candidates:
        for col in columns:
            if col == cand:
                return col
    for cand in candidates:
        for col in columns:
            if cand in col:
                return col
    return None


def _locate_header(raw: pd.DataFrame, max_scan: int = 40) -> int | None:
    """Bank exports often have a block of account info above the table; find
    the first row that looks like a header (has a date and description col)."""
    for i in range(min(max_scan, len(raw))):
        cells = [_norm(c) for c in raw.iloc[i].tolist()]
        if _find(cells, DATE_COLS) and _find(cells, DESC_COLS):
            return i
    return None


def frame_to_statement(raw: pd.DataFrame) -> ParsedStatement:
    """Turn a header-less raw table into transactions."""
    out = ParsedStatement()
    header_row = _locate_header(raw)
    if header_row is None:
        out.warnings.append("Couldn't find a header row with date + description columns.")
        return out

    df = raw.iloc[header_row + 1:].copy()
    df.columns = [_norm(c) for c in raw.iloc[header_row].tolist()]
    cols = list(df.columns)
    c_date, c_desc = _find(cols, DATE_COLS), _find(cols, DESC_COLS)
    c_debit, c_credit = _find(cols, DEBIT_COLS), _find(cols, CREDIT_COLS)
    c_amount, c_drcr = _find(cols, AMOUNT_COLS), _find(cols, DRCR_COLS)
    c_bal, c_ref = _find(cols, BALANCE_COLS), _find(cols, REF_COLS)

    if not (c_debit or c_credit or c_amount):
        out.warnings.append("Couldn't find amount / debit / credit columns.")
        return out

    last: ParsedTxn | None = None
    for _, row in df.iterrows():
        d = parse_date(row[c_date])
        desc = "" if pd.isna(row[c_desc]) else str(row[c_desc]).strip()
        if d is None:
            # Wrapped narration lines continue the previous transaction.
            if last is not None and desc and not any(
                parse_money(row[c]) for c in (c_debit, c_credit, c_amount) if c
            ):
                last.description = f"{last.description} {desc}".strip()
            continue

        debit = parse_money(row[c_debit]) if c_debit else None
        credit = parse_money(row[c_credit]) if c_credit else None
        if debit:
            amount, direction = debit, Direction.debit
        elif credit:
            amount, direction = credit, Direction.credit
        elif c_amount and (amount := parse_money(row[c_amount])):
            raw_amt = str(row[c_amount])
            marker = _norm(row[c_drcr]) if c_drcr else raw_amt.lower()
            is_credit = "cr" in marker or raw_amt.strip().startswith("+")
            direction = Direction.credit if is_credit else Direction.debit
        else:
            continue

        last = ParsedTxn(
            txn_date=d,
            description=desc,
            amount_minor=amount,
            direction=direction,
            balance_minor=parse_money(row[c_bal]) if c_bal else None,
            ref_no="" if not c_ref or pd.isna(row[c_ref]) else str(row[c_ref]).strip().lstrip("0"),
        )
        out.transactions.append(last)

    if not out.transactions:
        out.warnings.append("No transactions found in the table.")
    return out


def open_excel(path: Path, password: str | None) -> Path | io.BytesIO:
    """Return something pandas can read, decrypting password-protected
    workbooks (both .xlsx and legacy .xls) in memory."""
    import msoffcrypto
    from msoffcrypto.exceptions import DecryptionError, FileFormatError, InvalidKeyError

    with path.open("rb") as f:
        try:
            office = msoffcrypto.OfficeFile(f)
            encrypted = office.is_encrypted()
        except FileFormatError:
            return path
        if not encrypted:
            return path
        if not password:
            raise PasswordRequired(wrong_password=False)
        out = io.BytesIO()
        try:
            office.load_key(password=password)
            office.decrypt(out)
        except (InvalidKeyError, DecryptionError):
            raise PasswordRequired(wrong_password=True) from None
    out.seek(0)
    return out


@contextmanager
def open_pdf(path: Path, password: str | None):
    """pdfplumber.open that raises PasswordRequired for encrypted PDFs."""
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfplumber.utils.exceptions import PdfminerException

    try:
        pdf = pdfplumber.open(path, password=password or "")
    except PdfminerException as exc:
        if exc.args and isinstance(exc.args[0], PDFPasswordIncorrect):
            raise PasswordRequired(wrong_password=bool(password)) from None
        raise
    with pdf:
        yield pdf


@register
class GenericExcelParser(StatementParser):
    key = "generic_excel"
    display_name = "Generic Excel / CSV"
    extensions = (".xlsx", ".xls", ".csv")

    def parse(self, path: Path, password: str | None = None) -> ParsedStatement:
        if path.suffix.lower() == ".csv":
            raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
            return frame_to_statement(raw)
        engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
        sheets = pd.read_excel(open_excel(path, password), header=None, sheet_name=None, engine=engine)
        result = ParsedStatement()
        for name, raw in sheets.items():
            part = frame_to_statement(raw)
            result.transactions.extend(part.transactions)
            if not part.transactions:
                result.warnings.extend(f"[{name}] {w}" for w in part.warnings)
        return result


@register
class GenericPdfParser(StatementParser):
    key = "generic_pdf"
    display_name = "Generic PDF (table detection)"
    extensions = (".pdf",)

    def parse(self, path: Path, password: str | None = None) -> ParsedStatement:
        rows: list[list] = []
        with open_pdf(path, password) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    rows.extend(table)
        if not rows:
            return ParsedStatement(warnings=["No tables detected in PDF. A bank-specific parser is needed."])
        width = max(len(r) for r in rows)
        raw = pd.DataFrame([r + [None] * (width - len(r)) for r in rows])
        return frame_to_statement(raw)
