"""App settings.

Settings live in the per-machine config dir (never synced). The data dir (DB +
backups) can point anywhere, typically a Google Drive for Desktop folder so the
database syncs between machines.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "ExpenseKrafter"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / APP_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / APP_NAME / "data"
# Before it was renamed, the app kept its files under "ExpenseTracker".
LEGACY_CONFIG_DIR = CONFIG_DIR.with_name("ExpenseTracker")
LEGACY_DATA_DIR = DEFAULT_DATA_DIR.parent.with_name("ExpenseTracker") / "data"


@dataclass
class Settings:
    data_dir: str = str(DEFAULT_DATA_DIR)
    currency: str = "INR"
    # Alert when a card bill is due within this many days.
    due_alert_days: int = 5
    # How many startup backups of the DB to keep.
    backups_to_keep: int = 15
    # "light" or "dark"; per machine, like the rest of these settings.
    theme: str = "light"
    extra: dict = field(default_factory=dict)

    @property
    def data_path(self) -> Path:
        return Path(_data_dir_override or self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "expensekrafter.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_path / "backups"


# Set by `--data-dir` for development/testing. Kept out of Settings on purpose
# so saving settings (e.g. switching theme) never persists the throwaway folder.
_data_dir_override: str | None = None


def override_data_dir(path: str) -> None:
    global _data_dir_override
    _data_dir_override = path


def load_settings() -> Settings:
    if SETTINGS_FILE.exists():
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(**known)
    return Settings()


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def adopt_legacy_install() -> bool:
    """First run after the rename: copy settings, custom bank icons and the
    default data folder over from ExpenseTracker. The old folders are left
    untouched, as a fallback; a data folder you chose yourself (e.g. Google
    Drive) is kept where it is. Returns True if anything was copied."""
    copied = False
    # Keyed on settings.json, not the folder: the packaged app creates the
    # folder for its log before this runs.
    if not SETTINGS_FILE.exists() and LEGACY_CONFIG_DIR.exists():
        copied = True
        shutil.copytree(LEGACY_CONFIG_DIR, CONFIG_DIR, ignore=shutil.ignore_patterns("app.log"),
                        dirs_exist_ok=True)
        if SETTINGS_FILE.exists():
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
            if Path(raw.get("data_dir", "")) == LEGACY_DATA_DIR:
                raw["data_dir"] = str(DEFAULT_DATA_DIR)
                SETTINGS_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    if not DEFAULT_DATA_DIR.exists() and LEGACY_DATA_DIR.exists():
        shutil.copytree(LEGACY_DATA_DIR, DEFAULT_DATA_DIR, ignore=shutil.ignore_patterns("*.lock"))
        copied = True
    return copied


def reload_settings() -> None:
    """Re-read settings.json into the shared `settings` object (modules hold a
    reference to it, so it is updated in place)."""
    settings.__dict__.update(load_settings().__dict__)


settings = load_settings()
