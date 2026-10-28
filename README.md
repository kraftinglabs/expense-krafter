# Expense Tracker

Personal desktop expense tracker for multiple bank accounts and credit cards.
Python + [NiceGUI](https://nicegui.io) (native window) + Plotly + AG Grid + SQLite.

## Run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m expense_tracker            # desktop window
.venv/Scripts/python -m expense_tracker --browser  # http://localhost:8080 (dev)
.venv/Scripts/python -m pytest
```

`--data-dir <folder>` overrides the data folder (handy for a throwaway dev DB).

## Data & Google Drive sync

Settings → Data folder. Point it at a folder inside Google Drive for Desktop
(e.g. `G:\My Drive\ExpenseTracker`). The DB uses a rollback journal (not WAL) so
Drive syncs a single file, a lock file warns if another PC has it open, and a
backup is copied to `backups/` on every launch.

## Sensitive data

- **Statement passwords**: encrypted PDFs and Excel files (.xlsx/.xls) prompt
  for the password on import. With "Remember", the password goes into Windows
  Credential Manager (`ExpenseTracker statements`), per account, never into the
  DB, so it is not synced to Drive. Each machine remembers its own.
  Saved passwords can be removed on the Accounts page.
- **Account / card numbers**: entered in full, but only the first 5 characters
  are stored (`50100XXXXXXXXX`).

## Layout

```
expense_tracker/
  config.py            settings (per-machine, %APPDATA%\ExpenseTracker)
  db/models.py         schema: accounts, transactions, groups, labels,
                       card statements, payment links, reimbursement links
  db/session.py        engine, startup backup, single-writer lock
  importers/           statement parsers (one per bank/card format)
  services/            import + dedup, ledger queries, card bills, notifications
  ui/                  NiceGUI pages
```

## Adding a bank/card parser

Create `expense_tracker/importers/<bank>.py` with a `@register`-ed
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
