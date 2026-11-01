import json
import os
import socket

import pytest

from expensekrafter.config import Settings
from expensekrafter.services import inbox


@pytest.fixture(autouse=True)
def temp_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")


def test_sent_files_wait_in_the_inbox(tmp_path):
    pdf, txt = tmp_path / "Oct statement.PDF", tmp_path / "notes.txt"
    pdf.write_bytes(b"%PDF")
    txt.write_text("not a statement")
    added = inbox.add([pdf, txt, tmp_path / "missing.csv"])
    assert [inbox.original_name(p) for p in added] == ["Oct statement.PDF"]
    assert inbox.pending() == added and pdf.exists()  # a copy: the original stays
    inbox.remove(added[0])
    assert inbox.pending() == []


def test_app_running_follows_the_db_lock(tmp_path):
    cfg = Settings(data_dir=str(tmp_path / "data"))
    cfg.data_path.mkdir()
    lock = cfg.data_path / "expensekrafter.lock"
    assert not inbox.app_running(cfg)  # no lock
    me = socket.gethostname()
    lock.write_text(json.dumps({"host": me, "pid": os.getppid()}))
    assert inbox.app_running(cfg)  # another live process on this computer
    lock.write_text(json.dumps({"host": me, "pid": os.getpid()}))
    assert not inbox.app_running(cfg)  # that's us
    lock.write_text(json.dumps({"host": "other-pc", "pid": os.getppid()}))
    assert not inbox.app_running(cfg)
