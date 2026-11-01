"""Statements sent to ExpenseKrafter from outside (Explorer's "Send to" menu).

`expensekrafter --import FILE...` copies the files into an inbox folder. If the
app is already open it picks them up from there (the Import page lists them);
otherwise the launch carries on and the new window shows them.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from ..config import CONFIG_DIR, Settings
from ..db.session import lock_path

INBOX = CONFIG_DIR / "inbox"
SUFFIXES = {".pdf", ".xlsx", ".xls", ".csv"}
_SEP = "__"  # inbox name: <ms timestamp>__<original name>


def add(paths: list[str | Path]) -> list[Path]:
    """Copy statement files into the inbox. Anything else is ignored."""
    INBOX.mkdir(parents=True, exist_ok=True)
    added = []
    for p in map(Path, paths):
        if p.is_file() and p.suffix.lower() in SUFFIXES:
            dest = INBOX / f"{time.time_ns() // 1_000_000}{_SEP}{p.name}"
            shutil.copy2(p, dest)
            added.append(dest)
    return added


def pending() -> list[Path]:
    """Files waiting to be imported, oldest first."""
    if not INBOX.is_dir():
        return []
    return sorted((p for p in INBOX.iterdir() if p.is_file() and _SEP in p.name), key=lambda p: p.name)


def original_name(path: Path) -> str:
    return path.name.split(_SEP, 1)[1]


def remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def app_running(cfg: Settings) -> bool:
    """Is ExpenseKrafter already open on this computer (it holds the DB lock)?"""
    try:
        info = json.loads(lock_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = info.get("pid")
    return (info.get("host") == socket.gethostname() and isinstance(pid, int) and pid != os.getpid()
            and _alive(pid))


# ------------------------------------------------------------ Send to menu

SEND_TO = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/SendTo/ExpenseKrafter.lnk"


def _launch_command() -> tuple[str, str, str]:
    """(program, arguments, working folder) that open ExpenseKrafter with --import."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--import", str(Path(sys.executable).parent)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    root = Path(__file__).resolve().parents[2]
    return str(pythonw if pythonw.exists() else sys.executable), "-m expensekrafter --import", str(root)


def send_to_installed() -> bool:
    return SEND_TO.exists()


def install_send_to() -> None:
    """Add "Send to → ExpenseKrafter" to Explorer's right-click menu (Windows)."""
    program, args, cwd = _launch_command()
    icon = program if getattr(sys, "frozen", False) else str(Path(cwd) / "packaging" / "icon.ico")

    def ps(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps(str(SEND_TO))}); "
              f"$s.TargetPath = {ps(program)}; $s.Arguments = {ps(args)}; $s.WorkingDirectory = {ps(cwd)}; "
              f"$s.IconLocation = {ps(icon)}; $s.Description = 'Import this statement into ExpenseKrafter'; "
              "$s.Save()")
    SEND_TO.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def remove_send_to() -> None:
    SEND_TO.unlink(missing_ok=True)
