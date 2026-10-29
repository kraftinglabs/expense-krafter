"""Statement passwords, kept in the OS credential store (Windows Credential
Manager; on Linux the Secret Service, i.e. GNOME Keyring or KWallet), never in
the DB. They stay on this machine and are not synced to
Google Drive with the data folder.

Entries are keyed by the database's unique id plus the account id, so a
password saved for one database is never offered to another (a test DB, a
fresh DB after a reset, or a different data folder).
"""

from __future__ import annotations

import logging
import sys

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from ..db.session import database_id

SERVICE = "ExpenseKrafter statements"
LEGACY_SERVICE = "ExpenseTracker statements"  # before the rename
log = logging.getLogger(__name__)
# Where saved passwords go, in words for the UI.
STORE_NAME = "Windows Credential Manager" if sys.platform == "win32" else "your system keyring"


def _user(account_id: int) -> str:
    return f"{database_id()}/account-{account_id}"


def get_password(account_id: int) -> str | None:
    try:
        if (password := keyring.get_password(SERVICE, _user(account_id))) is not None:
            return password
        # Saved before the rename: move it over to the new name.
        if (password := keyring.get_password(LEGACY_SERVICE, _user(account_id))) is not None:
            keyring.set_password(SERVICE, _user(account_id), password)
            keyring.delete_password(LEGACY_SERVICE, _user(account_id))
        return password
    except KeyringError:
        log.exception("Couldn't read statement password from the credential store")
        return None


def set_password(account_id: int, password: str) -> bool:
    """False if there's no usable credential store (e.g. a Linux desktop
    without a keyring service); the password just isn't remembered then."""
    try:
        keyring.set_password(SERVICE, _user(account_id), password)
        return True
    except KeyringError:
        log.exception("Couldn't save statement password to the credential store")
        return False


def forget_password(account_id: int) -> None:
    try:
        keyring.delete_password(SERVICE, _user(account_id))
    except PasswordDeleteError:
        pass
    except KeyringError:  # no credential store at all (e.g. in Docker)
        log.exception("Couldn't remove statement password from the credential store")


def has_password(account_id: int) -> bool:
    return get_password(account_id) is not None
