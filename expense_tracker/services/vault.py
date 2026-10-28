"""Statement passwords, kept in the OS credential store (Windows Credential
Manager), never in the DB. They stay on this machine and are not synced to
Google Drive with the data folder.

Entries are keyed by the database's unique id plus the account id, so a
password saved for one database is never offered to another (a test DB, a
fresh DB after a reset, or a different data folder).
"""

from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from ..db.session import database_id

SERVICE = "ExpenseTracker statements"
log = logging.getLogger(__name__)


def _user(account_id: int) -> str:
    return f"{database_id()}/account-{account_id}"


def get_password(account_id: int) -> str | None:
    try:
        return keyring.get_password(SERVICE, _user(account_id))
    except KeyringError:
        log.exception("Couldn't read statement password from the credential store")
        return None


def set_password(account_id: int, password: str) -> None:
    keyring.set_password(SERVICE, _user(account_id), password)


def forget_password(account_id: int) -> None:
    try:
        keyring.delete_password(SERVICE, _user(account_id))
    except PasswordDeleteError:
        pass


def has_password(account_id: int) -> bool:
    return get_password(account_id) is not None
