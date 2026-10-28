from __future__ import annotations

from decimal import Decimal


def to_major(minor: int | None) -> float | None:
    return None if minor is None else minor / 100


def to_minor(major: float | str | Decimal) -> int:
    return int((Decimal(str(major)) * 100).quantize(Decimal(1)))


def fmt(minor: int | None, symbol: str = "₹") -> str:
    if minor is None:
        return ""
    sign = "-" if minor < 0 else ""
    return f"{sign}{symbol}{abs(minor) / 100:,.2f}"
