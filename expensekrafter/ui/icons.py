"""Bank icons for accounts.

Built-in icons live in ui/bank_icons/. Drop your own square SVG/PNG files into
the user folder (%APPDATA%\\ExpenseKrafter\\bank_icons) and they appear in the
account icon picker; the file name is the icon's name (hdfc.svg -> "Hdfc").
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from nicegui import app

from ..config import CONFIG_DIR
from ..db.models import Account, AccountType

BUILTIN_DIR = Path(__file__).with_name("bank_icons")
USER_DIR = CONFIG_DIR / "bank_icons"
EXTENSIONS = {".svg", ".png", ".webp", ".jpg", ".jpeg"}

LABELS = {"sbi": "SBI", "federal": "Federal Bank"}
# Words in an account's bank / name that pick an icon automatically.
KEYWORDS = {"sbi": ["sbi", "state bank"], "federal": ["federal"]}

USER_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files("/bank-icons", BUILTIN_DIR)
app.add_static_files("/bank-icons-user", USER_DIR)


def available() -> dict[str, tuple[str, str]]:
    """{key: (label, url)}; user files override built-ins with the same name."""
    found: dict[str, tuple[str, str]] = {}
    for folder, prefix in ((BUILTIN_DIR, "/bank-icons"), (USER_DIR, "/bank-icons-user")):
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() in EXTENSIONS:
                label = LABELS.get(f.stem, f.stem.replace("_", " ").replace("-", " ").title())
                found[f.stem] = (label, f"{prefix}/{f.name}")
    return found


def options() -> dict[str, str]:
    """Choices for the account icon picker."""
    return {"": "Automatic (from bank name)"} | {k: lbl for k, (lbl, _) in available().items()} | {
        "generic": "Generic icon"}


def resolve(account: Account) -> str | None:
    """Icon key to show for an account: its chosen icon, else a guess from its
    bank / name, else None (generic tile)."""
    icons = available()
    if account.icon == "generic":
        return None
    if account.icon in icons:
        return account.icon
    haystack = f"{account.institution} {account.name}".lower()
    for key in icons:
        words = KEYWORDS.get(key, [key])
        if any(w in haystack for w in words):
            return key
    return None


def tile_html(account: Account, size: int = 44) -> str:
    """Square icon tile for an account (bank logo, or a generic tile)."""
    key = resolve(account)
    radius = round(size * 0.27)
    if key:
        label, url = available()[key]
        return (f'<img src="{url}" alt="{escape(label)}" title="{escape(label)}" '
                f'style="width:{size}px;height:{size}px;border-radius:{radius}px;display:block;flex:none">')
    is_card = account.type == AccountType.credit_card
    tile = "et-tile-card" if is_card else "et-tile-bank"
    glyph = "credit_card" if is_card else "account_balance"
    return (f'<div class="et-avatar {tile}" style="width:{size}px;height:{size}px;border-radius:{radius}px">'
            f'<i class="q-icon notranslate material-icons" style="font-size:{round(size * 0.48)}px">{glyph}</i></div>')
