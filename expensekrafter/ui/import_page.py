from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from nicegui import events, ui

from ..db.models import TxnKind
from ..importers import PasswordRequired, parser_options
from ..services import ledger, narration, vault
from ..services.importer import ImportPreview, default_kind, commit_import, preview_import
from ..services.money import fmt
from .layout import frame
from .transactions import KIND_LABELS


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
            # Pre-ticked: every new row except ones you skipped in an earlier import.
            state["sel"] = {r.fingerprint for r in p.new_rows if not r.skipped_before}
            payments = {r.fingerprint for r in p.new_rows
                        if default_kind(p.account, r.txn.direction, r.txn.description) == TxnKind.cc_payment}
            skipped_before = sum(r.skipped_before and not r.duplicate for r in p.rows)
            result.clear()
            with result:
                with ui.card().classes("w-full"):
                    ui.label(f"{p.filename} → {p.account.name}").classes("font-medium")
                    if p.already_imported_file:
                        ui.label("This exact file was imported before.").classes("et-warn")
                    for w in p.statement.warnings:
                        ui.label(w).classes("et-warn")
                    ui.label(f"{len(p.rows)} found · {len(p.new_rows)} new · {p.duplicate_count} already imported"
                             + (f" · {skipped_before} skipped last time" if skipped_before else "")) \
                        .classes("text-sm et-muted")
                    with ui.row().classes("w-full items-center gap-2 mt-2"):
                        summary = ui.label().classes("et-num text-sm")
                        ui.space()
                        if p.new_rows:
                            ui.button("Tick all new", icon="done_all",
                                      on_click=lambda: set_selection({r.fingerprint for r in p.new_rows})) \
                                .props("flat dense no-caps")
                        if payments:
                            ui.button(f"Untick card bill payments ({len(payments)})", icon="remove_done",
                                      on_click=lambda: set_selection(state["sel"] - payments)) \
                                .props("flat dense no-caps") \
                                .tooltip("Bill payments are usually already in your bank account's statement")
                        import_btn = ui.button(icon="check", on_click=do_commit)
                    ui.label("Unticked rows are remembered for this account and start unticked if they "
                             "appear again.").classes("text-xs et-faint")
                grid_box = ui.column().classes("w-full")
            state["summary"], state["import_btn"], state["grid_box"] = summary, import_btn, grid_box
            render_grid()
            update_summary()

        def render_grid() -> None:
            p: ImportPreview = state["preview"]
            box = state["grid_box"]
            box.clear()
            with box:
                grid = ui.aggrid({
                    "columnDefs": [
                        {"field": "status", "width": 165},
                        {"field": "date", "width": 125},
                        {"field": "description", "flex": 1, "minWidth": 240, "tooltipField": "raw",
                         "wrapText": True, "autoHeight": True},
                        {"field": "type", "width": 130},
                        {"field": "amount", "width": 130, "type": "rightAligned"},
                    ],
                    "rowData": [{
                        "fp": r.fingerprint,
                        "status": "already imported" if r.duplicate else ("skipped before" if r.skipped_before
                                                                          else "new"),
                        "date": r.txn.txn_date.isoformat(),
                        "description": narration.parse(r.txn.description).display,
                        "raw": re.sub(r"\s+", " ", r.txn.description),
                        "type": KIND_LABELS[default_kind(p.account, r.txn.direction, r.txn.description)],
                        "amount": ("−" if r.txn.direction.value == "debit" else "+") + fmt(r.txn.amount_minor),
                        "pre": r.fingerprint in state["sel"],
                    } for r in p.rows],
                    ":getRowId": "p => p.data.fp",
                    "rowSelection": {
                        "mode": "multiRow", "checkboxes": True, "headerCheckbox": True,
                        "hideDisabledCheckboxes": True,
                        ":isRowSelectable": "n => n.data && n.data.status !== 'already imported'",
                    },
                    ":onFirstDataRendered": "p => { const nodes = []; p.api.forEachNode(n => n.data.pre && "
                                            "nodes.push(n)); p.api.setNodesSelected({nodes, newValue: true}); }",
                    ":rowClassRules": "{'opacity-40': p => p.data.status === 'already imported'}",
                    "defaultColDef": {"sortable": True, "resizable": True},
                }).classes("w-full").style("height: 28rem")

                async def on_selection() -> None:
                    state["sel"] = {r["fp"] for r in await grid.get_selected_rows()}
                    update_summary()

                grid.on("selectionChanged", on_selection)

        def set_selection(fps: set[str]) -> None:
            state["sel"] = set(fps)
            render_grid()
            update_summary()

        def update_summary() -> None:
            p: ImportPreview = state["preview"]
            chosen = [r for r in p.new_rows if r.fingerprint in state["sel"]]
            out = sum(r.txn.amount_minor for r in chosen if r.txn.direction.value == "debit")
            inn = sum(r.txn.amount_minor for r in chosen if r.txn.direction.value == "credit")
            state["summary"].text = (f"{len(chosen)} of {len(p.new_rows)} new selected"
                                     + (f" · out {fmt(out)}" if out else "") + (f" · in {fmt(inn)}" if inn else ""))
            btn = state["import_btn"]
            btn.text = (f"Import {len(chosen)} transaction{'s' if len(chosen) != 1 else ''}" if chosen
                        else "Save bill only")
            # A card statement's bill summary is worth importing even with no rows ticked.
            btn.set_enabled(bool(chosen) or p.statement.card_summary is not None)

        def do_commit() -> None:
            p: ImportPreview = state["preview"]
            batch = commit_import(p, selected=state["sel"])
            skipped = len(p.new_rows) - batch.rows_new
            ui.notify(f"Imported {batch.rows_new} transactions"
                      + (f", skipped {skipped}" if skipped else "")
                      + (f", {batch.rows_duplicate} already imported" if batch.rows_duplicate else ""),
                      type="positive")
            result.clear()

        ui.label("Tip: the generic parsers handle most Excel exports. Share a sample PDF per bank/card "
                 "to get a dedicated parser. Password-protected files will ask for the password.") \
            .classes("text-xs et-faint")
