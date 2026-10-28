"""Transaction fingerprints for duplicate detection across overlapping exports.

The fingerprint must be identical for the same real-world transaction no matter
which export window it came from:
  - With a bank reference number, that plus amount/direction is enough.
  - Otherwise date + amount + direction + normalised description + running
    balance. The balance separates two identical purchases on one day.
  - With neither ref nor balance (common for card statements), identical rows
    within a file get an occurrence index (#1, #2) so two genuine same-day
    coffees aren't collapsed, while re-importing the same file still dedups.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from ..importers.base import ParsedTxn


def normalise_description(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _base_key(account_id: int, t: ParsedTxn) -> str:
    if t.ref_no:
        return f"{account_id}|ref|{t.ref_no}|{t.amount_minor}|{t.direction.value}"
    parts = [str(account_id), t.txn_date.isoformat(), str(t.amount_minor), t.direction.value,
             normalise_description(t.description)]
    if t.balance_minor is not None:
        parts.append(f"bal{t.balance_minor}")
    return "|".join(parts)


def fingerprints(account_id: int, txns: list[ParsedTxn]) -> list[str]:
    seen: Counter[str] = Counter()
    out = []
    for t in txns:
        key = _base_key(account_id, t)
        seen[key] += 1
        out.append(hashlib.sha1(f"{key}#{seen[key]}".encode()).hexdigest())
    return out
