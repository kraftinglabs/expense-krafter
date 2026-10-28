"""App settings.

Settings live in the per-machine config dir (never synced). The data dir (DB +
backups) can point anywhere, typically a Google Drive for Desktop folder so the
database syncs between machines.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "ExpenseTracker"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / APP_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / APP_NAME / "data"


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
        return self.data_path / "expense_tracker.db"

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
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(**known)
    return Settings()


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


settings = load_settings()
