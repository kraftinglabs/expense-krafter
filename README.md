# ExpenseKrafter

Personal desktop expense tracker for multiple bank accounts and credit cards,
part of [Kraftinglabs](https://kraftinglabs.com).
Python + [NiceGUI](https://nicegui.io) (native window) + Plotly + AG Grid + SQLite.

## Run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m expensekrafter            # desktop window
.venv/Scripts/python -m expensekrafter --browser  # http://localhost:8080 (dev)
.venv/Scripts/python -m pytest
```

`--data-dir <folder>` overrides the data folder (handy for a throwaway dev DB).

## Build the desktop app

Build on the OS you're targeting (PyInstaller can't cross-compile).

**Windows**

```bash
.venv/Scripts/python -m pip install -e ".[dev,build]"
.venv/Scripts/python build.py              # dist/ExpenseKrafter/ExpenseKrafter.exe
.venv/Scripts/python build.py --onefile    # a single dist/ExpenseKrafter.exe (starts slower)
```

**Linux** (Ubuntu/Debian package names shown; Python 3.11+)

```bash
sudo apt install python3-venv libxcb-cursor0 libegl1 libnss3   # Qt WebEngine runtime libraries
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,build]"   # pulls in PyQt6 + WebEngine on Linux
.venv/bin/python build.py
```

This gives `dist/ExpenseKrafter/ExpenseKrafter` and
`dist/ExpenseKrafter-linux-<arch>.tar.gz`. Unpack the archive on any Linux PC
and run `./install.sh`: it copies the app to `~/.local/opt/ExpenseKrafter`, adds
it to the app menu and an `expensekrafter` command to `~/.local/bin`
(`./install.sh --uninstall` removes them again; your data stays).

The tests run first; the build stops if they fail (`--skip-tests` to skip).
The app uses the same data folder and settings as the dev version; no data is
bundled. A windowed build has no console, so its log goes to `app.log` in the
settings folder. The icon (a pixel-art wallet in the
[Kraftinglabs](https://kraftinglabs.com) style) is drawn by
`packaging/make_icon.py`; edit its pixel map and re-run it to change the icon.

## Where things are saved

| | Windows | Linux |
|---|---|---|
| Database + backups | `%LOCALAPPDATA%\ExpenseKrafter\data` | `~/Documents/ExpenseKrafter` (your XDG Documents folder) |
| Settings, log, own bank icons | `%APPDATA%\ExpenseKrafter` | `~/.config/ExpenseKrafter` |
| Saved statement passwords | Windows Credential Manager | Secret Service (GNOME Keyring / KWallet) |

The database folder can be changed in Settings → Data folder.

## Data & Google Drive sync

Settings → Data folder. Point it at a folder inside Google Drive for Desktop
(e.g. `G:\My Drive\ExpenseKrafter`). The DB uses a rollback journal (not WAL) so
Drive syncs a single file, a lock file warns if another PC has it open, and a
backup is copied to `backups/` on every launch.

## Sensitive data

- **Statement passwords**: encrypted PDFs and Excel files (.xlsx/.xls) prompt
  for the password on import. With "Remember", the password goes into Windows
  Credential Manager (`ExpenseKrafter statements`), per account, never into the
  DB, so it is not synced to Drive. Each machine remembers its own.
  Saved passwords can be removed on the Accounts page.
- **Account / card numbers**: entered in full, but only the first 5 characters
  are stored (`50100XXXXXXXXX`).

## Layout

```
expensekrafter/
  config.py            settings (per-machine, %APPDATA%\ExpenseKrafter)
  db/models.py         schema: accounts, transactions, groups, labels,
                       card statements, payment links, reimbursement links
  db/session.py        engine, startup backup, single-writer lock
  importers/           statement parsers (one per bank/card format)
  services/            import + dedup, ledger queries, card bills, notifications
  ui/                  NiceGUI pages
```

## Adding a bank/card parser

Create `expensekrafter/importers/<bank>.py` with a `@register`-ed
`StatementParser` that returns a `ParsedStatement` (transactions, and for cards
a `CardSummary` with due date and total). Import it in `importers/__init__.py`.
Dedup and saving are handled for you.

## Status

| Use case | State |
|---|---|
| Import Excel/CSV/PDF, monthly tables | Generic parsers; bank-specific ones need samples |
| Labels | Inline editing in the grid |
| Group transactions | Done |
| Card statements ↔ bank payments, pending bills | Manual statement entry + payment linking |
| Splits / reimbursements | Schema ready, UI next |
| Recurring | Recurring labels; auto-detection next |
| Due-date alerts | Desktop notification on launch and every 6 h |
| Duplicate detection | Done (fingerprints) |
