import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    """In-memory store so tests never touch the real Credential Manager."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def default_narration_rules(tmp_path, monkeypatch):
    """Ignore any personal rules copy in the real config dir."""
    from expensekrafter.services import narration

    monkeypatch.setattr(narration, "USER_RULES", tmp_path / "no-user-rules.toml")


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
