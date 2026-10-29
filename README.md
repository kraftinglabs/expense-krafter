# ExpenseKrafter

Personal expense tracker for multiple bank accounts and credit cards, as a
desktop app or a web app, part of [Kraftinglabs](https://kraftinglabs.com).
Python + [NiceGUI](https://nicegui.io) + Plotly + AG Grid + SQLite.

## Run as a web app (any OS)

The most portable way: it runs in your browser, so it works the same on
Windows, macOS and any Linux desktop (including Wayland compositors like
Hyprland). Needs Python 3.11+ and [pipx](https://pipx.pypa.io).

```bash
pipx install /path/to/ExpenseKrafter     # a clone of this repo (or git+https://github.com/<you>/ExpenseKrafter)
expensekrafter --web                     # serves http://localhost:8080 and opens your browser
```

On Arch / CachyOS: `sudo pacman -S python-pipx` first. Stop the app with Ctrl+C;
your data stays in `~/Documents/ExpenseKrafter` (see "Where things are saved").

By default it only listens on your own computer. To reach it from other devices
(`--host 0.0.0.0`) it **requires a password**, read from the
`EXPENSEKRAFTER_PASSWORD` environment variable, and shows a sign-in page. Put it
behind HTTPS (e.g. a Caddy or nginx reverse proxy) before using it over a
network you don't trust: plain HTTP sends the password unencrypted.

### Docker

```bash
docker build -t expensekrafter .
docker run -d --name expensekrafter -p 127.0.0.1:8080:8080 \
    -v ~/Documents/ExpenseKrafter:/data -e EXPENSEKRAFTER_PASSWORD='choose-one' expensekrafter
```

Everything (database, backups, settings) lives in the mounted `/data` folder.
For a container that only you can reach (published on `127.0.0.1` as above),
you can skip the password by appending
`expensekrafter --web --host 0.0.0.0 --no-open --no-password` to `docker run`.

### What's different from the desktop app

- Card due-date reminders show on the dashboard ("Card bills pending") instead
  of as desktop pop-ups.
- Statement passwords are remembered only where a keyring is available (not in
  Docker); otherwise you type them on each import.

## Run from source

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,desktop]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m expensekrafter            # desktop window
.venv/Scripts/python -m expensekrafter --web      # web app in your browser
.venv/Scripts/python -m pytest
```

`expensekrafter --help` lists all options. `--data-dir <folder>` uses another
data folder for one run (handy for a throwaway dev DB).

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
# Build tools, plus the libraries Qt WebEngine needs (a desktop install has most already)
sudo apt install python3-venv libpython3-dev binutils git \
    libegl1 libgl1 libnss3 libglib2.0-0t64 libfontconfig1 libdbus-1-3 libcups2t64 \
    libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
    libxcb-render-util0 libxcb-image0 libxcb-randr0 libxcomposite1 libxdamage1 libxrandr2 \
    libxfixes3 libxkbfile1 libxtst6 libxshmfence1 libasound2t64 libwayland-cursor0 libwayland-egl1
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

- **App password**: Settings → App password. Asked for every time the app
  opens (desktop or web); Sign out in the sidebar locks it. Only a salted
  scrypt hash is stored, inside the database, so it travels with your data. It
  locks the app, not the database file. Forgot it? `expensekrafter
  --reset-password` removes it (the database is backed up first).

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
