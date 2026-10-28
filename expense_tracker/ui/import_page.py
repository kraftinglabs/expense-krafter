from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from nicegui import events, ui

from ..importers import PasswordRequired, parser_options
from ..services import ledger, vault
from ..services.importer import ImportPreview, commit_import, preview_import
from ..services.money import fmt
from .layout import frame


@ui.page("/import")
def import_page() -> None:
    with frame("Import", "Bring in bank and card statements. Duplicates are skipped automatically"):
        accounts = ledger.accounts()
        if not accounts:
            ui.label("Add an account first.").classes("et-muted")
            ui.link("Go to Accounts", "/accounts")
            return
        by_id = {a.id: a for a in accounts}
        state: dict = {"preview": None}

        with ui.card().classes("w-full"):
            with ui.row().classes("items-end gap-4"):
                account = ui.select({a.id: a.name for a in accounts}, value=accounts[0].id,
                                    label="Account").classes("w-56")
                parser = ui.select(parser_options(), value=accounts[0].parser_key, label="Format").classes("w-64")
                account.on_value_change(lambda e: parser.set_value(by_id[e.value].parser_key))
            ui.upload(label="Drop a statement (PDF, XLSX, XLS, CSV)", auto_upload=True,
                      on_upload=lambda e: handle_upload(e)).props("accept=.pdf,.xlsx,.xls,.csv").classes("w-full")

        result = ui.column().classes("w-full")
        # Dialogs opened from the upload handler would otherwise be created
        # inside the uploader, which re-renders and drops them.
        dialog_host = ui.element()

        async def handle_upload(e: events.UploadEventArguments) -> None:
            tmp = Path(tempfile.mkdtemp()) / e.file.name
            await e.file.save(tmp)
            acc_id = account.value
            password = vault.get_password(acc_id)
            from_vault = password is not None
            try:
                while True:
                    try:
                        state["preview"] = preview_import(acc_id, tmp, parser.value, e.file.name, password)
                        break
                    except PasswordRequired as exc:
                        answer = await ask_password(e.file.name, wrong=exc.wrong_password, saved=from_vault)
                        if answer is None:
                            return
                        password, remember = answer
                        from_vault = False
                        if remember:
                            state["remember"] = password
            except Exception as exc:  # parsers raise all sorts on malformed files
                ui.notify(f"Couldn't read {e.file.name}: {exc}", type="negative", multi_line=True)
                return
            finally:
                shutil.rmtree(tmp.parent, ignore_errors=True)
            if remember_pw := state.pop("remember", None):
                vault.set_password(acc_id, remember_pw)
                ui.notify("Password saved for this account on this computer", type="info")
            show_preview()

        async def ask_password(filename: str, wrong: bool, saved: bool) -> tuple[str, bool] | None:
            dialog_host.clear()
            with dialog_host, ui.dialog().props("persistent") as dlg, ui.card().classes("w-96"):
                ui.label(f"{filename} is password protected").classes("font-medium")
                if wrong:
                    ui.label("The saved password didn't work." if saved else "Wrong password, try again.") \
                        .classes("et-neg text-sm")
                pw = ui.input("Password", password=True, password_toggle_button=True).classes("w-full") \
                    .props("autofocus autocomplete=off")
                remember = ui.checkbox("Remember for this account on this computer", value=True)
                ui.label("Stored in Windows Credential Manager, not in the database or Google Drive.") \
                    .classes("text-xs et-muted")
                def unlock() -> None:
                    if pw.value:
                        dlg.submit((pw.value, remember.value))

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
                    ui.button("Unlock", on_click=unlock)
                pw.on("keydown.enter", unlock)
            return await dlg

        def show_preview() -> None:
            p: ImportPreview = state["preview"]
            result.clear()
            with result:
                with ui.card().classes("w-full"):
                    ui.label(f"{p.filename} → {p.account.name}").classes("font-medium")
                    if p.already_imported_file:
                        ui.label("This exact file was imported before.").classes("et-warn")
                    for w in p.statement.warnings:
                        ui.label(w).classes("et-warn")
                    ui.label(f"{len(p.rows)} transactions found · {len(p.new_rows)} new · "
                             f"{p.duplicate_count} duplicates will be skipped")
                    ui.button(f"Import {len(p.new_rows)} transactions", icon="check",
                              on_click=do_commit).props("" if p.new_rows else "disable")
                ui.aggrid({
                    "columnDefs": [{"field": f} for f in ("status", "date", "description", "debit", "credit", "balance")],
                    "rowData": [{
                        "status": "duplicate" if r.duplicate else "new",
                        "date": r.txn.txn_date.isoformat(),
                        "description": r.txn.description,
                        "debit": fmt(r.txn.amount_minor) if r.txn.direction.value == "debit" else "",
                        "credit": fmt(r.txn.amount_minor) if r.txn.direction.value == "credit" else "",
                        "balance": fmt(r.txn.balance_minor),
                    } for r in p.rows],
                    ":rowClassRules": "{'opacity-40': p => p.data.status === 'duplicate'}",
                }).classes("w-full h-96")

        def do_commit() -> None:
            batch = commit_import(state["preview"])
            ui.notify(f"Imported {batch.rows_new} transactions ({batch.rows_duplicate} duplicates skipped)",
                      type="positive")
            result.clear()

        ui.label("Tip: the generic parsers handle most Excel exports. Share a sample PDF per bank/card "
                 "to get a dedicated parser. Password-protected files will ask for the password.") \
            .classes("text-xs et-faint")
