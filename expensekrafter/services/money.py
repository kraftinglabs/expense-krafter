from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

# "Hide amounts" (the eye button): every formatted amount becomes this.
MASK = "****"
_hidden_check: Callable[[], bool] = lambda: False


def set_hidden_check(check: Callable[[], bool]) -> None:
    """The UI tells us how to look up whether amounts are hidden right now."""
    global _hidden_check
    _hidden_check = check


def hidden() -> bool:
    try:
        return bool(_hidden_check())
    except Exception:  # noqa: BLE001  (no UI context, e.g. tests or background jobs)
        return False


def masked(symbol: str = "₹") -> str:
    return f"{symbol}{MASK}"


def to_major(minor: int | None) -> float | None:
    return None if minor is None else minor / 100


def to_minor(major: float | str | Decimal) -> int:
    return int((Decimal(str(major)) * 100).quantize(Decimal(1)))


def fmt(minor: int | None, symbol: str = "₹") -> str:
    if minor is None:
        return ""
    if hidden():
        return masked(symbol)
    sign = "-" if minor < 0 else ""
    return f"{sign}{symbol}{abs(minor) / 100:,.2f}"
