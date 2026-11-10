from __future__ import annotations

import re
import shutil
import tempfile
from datetime import date, timezone
from pathlib import Path

from nicegui import events, ui

from ..db.models import AccountType, Direction, TxnKind
from ..importers import PasswordRequired, parser_options
from ..services import analytics, importer, inbox, ledger, narration, splits, vault
from ..services.importer import ImportPreview, default_kind, commit_import, preview_import
from ..services.money import fmt
from . import icons
from .layout import frame, on_hide_change
from .transactions import KIND_LABELS

# A bank statement older than this probably means a month hasn't been imported.
STALE_DAYS = 35


def _ago(d: date) -> str:
    days = (date.today() - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"


def account_statuses() -> None:
    """One card per account: its latest transaction and when it was last imported."""
    statuses = importer.account_statuses()
    ui.label("Where each account is up to").classes("et-card-title")
    with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-4 w-full"):
        for st in statuses:
            t = st.last_txn
            stale = t is not None and (date.today() - t.txn_date).days > STALE_DAYS
            with ui.card().classes("et-card-hover gap-2"):
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    ui.html(icons.tile_html(st.account, 32), sanitize=False).classes("flex-none")
                    with ui.column().classes("gap-0 min-w-0 flex-1"):
                        ui.label(st.account.name).classes("font-semibold truncate w-full")
                        ui.label(f"{st.txn_count:,} transaction{'s' if st.txn_count != 1 else ''}") \
                            .classes("text-xs et-faint")
                    if t is None:
                        ui.label("No data yet").classes("et-chip warn flex-none")
                    elif stale:
                        ui.label("Due for an import").classes("et-chip warn flex-none") \
                            .tooltip(f"Nothing newer than {STALE_DAYS} days")
                    else:
                        ui.label("Up to date").classes("et-chip accent flex-none")
                if t is None:
                    ui.label("Import a statement to see it here.").classes("text-sm et-muted")
                    continue
                with ui.row().classes("w-full items-baseline gap-2 no-wrap"):
                    ui.label(f"{t.txn_date:%d %b %Y}").classes("text-sm font-medium flex-none")
                    ui.label(f"· {_ago(t.txn_date)}").classes("text-xs " + ("et-warn" if stale else "et-faint"))
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label(analytics.merchant_name(t.description)).classes("text-sm et-muted flex-1 truncate") \
                        .tooltip(re.sub(r"\s+", " ", t.description))
                    sign = "−" if t.direction == Direction.debit else "+"
                    ui.label(f"{sign}{fmt(t.amount_minor)}").classes(
                        "et-num text-sm font-medium flex-none " + ("" if t.direction == Direction.debit else "et-pos"))
                if t.balance_minor is not None and st.account.type != AccountType.credit_card:
                    ui.label(f"Balance after it: {fmt(t.balance_minor)}").classes("text-xs et-faint et-num")
                if (b := st.last_import) is not None:
                    when = b.imported_at.replace(tzinfo=timezone.utc).astimezone()
                    ui.label(f"Last import {when:%d %b %Y}: {b.filename}").classes("text-xs et-faint truncate w-full") \
                        .tooltip(b.filename)


def import_history() -> None:
    """Every statement imported so far, newest first."""
    rows = importer.import_history()
    formats = parser_options()
    with ui.row().classes("w-full items-baseline gap-2"):
        ui.label("Previous imports").classes("et-card-title")
        ui.label(f"{len(rows)} file{'s' if len(rows) != 1 else ''}").classes("text-xs et-faint")
    if not rows:
        ui.label("Nothing imported yet.").classes("et-muted text-sm")
        return
    table = ui.table(
        columns=[
            {"name": "when", "label": "Imported", "field": "when", "align": "left", "sortable": True},
            {"name": "account", "label": "Account", "field": "account", "align": "left", "sortable": True},
            {"name": "file", "label": "File", "field": "file", "align": "left"},
            {"name": "period", "label": "Covers", "field": "period", "align": "left"},
            {"name": "new", "label": "Added", "field": "new", "align": "right", "sortable": True},
            {"name": "dup", "label": "Already there", "field": "dup", "align": "right", "sortable": True},
            {"name": "total", "label": "Rows", "field": "total", "align": "right", "sortable": True},
            {"name": "format", "label": "Format", "field": "format", "align": "left"},
        ],
        rows=[{
            "id": r.batch.id,
            "when": f"{r.batch.imported_at.replace(tzinfo=timezone.utc).astimezone():%Y-%m-%d %H:%M}",
            "account": r.account, "file": r.batch.filename,
            "period": (f"{r.batch.period_start:%d %b %Y} – {r.batch.period_end:%d %b %Y}"
                       if r.batch.period_start and r.batch.period_end else "—"),
            "new": r.batch.rows_new, "dup": r.batch.rows_duplicate, "total": r.batch.rows_total,
            "format": formats.get(r.batch.parser_key, r.batch.parser_key),
        } for r in rows],
        row_key="id", pagination={"rowsPerPage": 15, "sortBy": "when", "descending": True},
    ).props("flat dense").classes("w-full")
    table.add_slot("body-cell-file", r'''
        <q-td :props="props"><div class="truncate max-w-64" :title="props.value">{{ props.value }}</div></q-td>''')
    table.add_slot("body-cell-format", r'''
        <q-td :props="props"><div class="truncate max-w-36 et-faint" :title="props.value">{{ props.value }}</div></q-td>''')
    table.add_slot("body-cell-new", r'''
        <q-td :props="props" :class="props.value ? '' : 'et-faint'">{{ props.value }}</q-td>''')


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

        @ui.refreshable
        def sent() -> None:
            files = inbox.pending()
            state["inbox_names"] = [f.name for f in files]
            if not files:
                return
            with ui.card().classes("w-full gap-2 border-l-4 border-emerald-600"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("move_to_inbox").classes("et-pos text-xl")
                    ui.label(f"Sent to ExpenseKrafter ({len(files)})").classes("font-medium")
                ui.label("Pick the account and format above, then preview each file.").classes("text-xs et-muted")
                for f in files:
                    with ui.row().classes("w-full items-center gap-3 no-wrap"):
                        ui.icon("description").classes("et-faint")
                        ui.label(inbox.original_name(f)).classes("flex-1 truncate text-sm")
                        ui.button("Preview", icon="visibility",
                                  on_click=lambda f=f: preview_file(f, inbox.original_name(f), from_inbox=f)) \
                            .props("outline dense no-caps")
                        ui.button(icon="close", on_click=lambda f=f: (inbox.remove(f), sent.refresh())) \
                            .props("flat round dense").tooltip("Remove from this list (the original file stays)")

        sent()
        ui.timer(2, lambda: sent.refresh() if [f.name for f in inbox.pending()] != state.get("inbox_names")
                 else None)

        result = ui.column().classes("w-full")
        # Dialogs opened from the upload handler would otherwise be created
        # inside the uploader, which re-renders and drops them.
        dialog_host = ui.element()

        async def handle_upload(e: events.UploadEventArguments) -> None:
            tmp = Path(tempfile.mkdtemp()) / e.file.name
            await e.file.save(tmp)
            try:
                await preview_file(tmp, e.file.name)
            finally:
                shutil.rmtree(tmp.parent, ignore_errors=True)

        async def preview_file(path: Path, name: str, from_inbox: Path | None = None) -> None:
            acc_id = account.value
            password = vault.get_password(acc_id)
            from_vault = password is not None
            try:
                while True:
                    try:
                        state["preview"] = preview_import(acc_id, path, parser.value, name, password)
                        break
                    except PasswordRequired as exc:
                        answer = await ask_password(name, wrong=exc.wrong_password, saved=from_vault)
                        if answer is None:
                            return
                        password, remember = answer
                        from_vault = False
                        if remember:
                            state["remember"] = password
            except Exception as exc:  # parsers raise all sorts on malformed files
                ui.notify(f"Couldn't read {name}: {exc}", type="negative", multi_line=True)
                return
            state["inbox_file"] = from_inbox
            if remember_pw := state.pop("remember", None):
                if vault.set_password(acc_id, remember_pw):
                    ui.notify("Password saved for this account on this computer", type="info")
                else:
                    ui.notify(f"Couldn't save the password: {vault.STORE_NAME} isn't available",
                              type="warning")
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
                ui.label(f"Stored in {vault.STORE_NAME}, not in the database or Google Drive.") \
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
            if done := state.pop("inbox_file", None):
                inbox.remove(done)
                sent.refresh()
            overview.refresh()
            if matches := splits.pending_matches(importer.batch_txn_ids(batch.id)):
                attach_pending(matches)

        def attach_pending(matches) -> None:
            """Pending splits that showed up in this statement: attach them in one go."""
            picks = []
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[44rem] max-w-full gap-3"):
                ui.label("Splits waiting for this statement").classes("et-card-title")
                ui.label("These look like splits you recorded before the statement came in. Attach them and "
                         "their shares and repayments move onto the imported transactions.") \
                    .classes("text-sm et-muted")
                for sp, found in matches:
                    with ui.column().classes("w-full gap-1 et-hero-rule pt-2"):
                        tick = ui.checkbox(f"{sp.name} · {sp.txn_date:%d %b %Y} · {fmt(sp.amount_minor)}", value=True)
                        choice = ui.select({t.id: f"{t.txn_date:%d %b %Y} · {analytics.merchant_name(t.description)}"
                                                  f" · {fmt(t.amount_minor)}" for t, _ in found},
                                           value=found[0][0].id).props("outlined dense options-dense") \
                            .classes("w-full pl-8")
                        choice.bind_enabled_from(tick, "value")
                        picks.append((sp, tick, choice))

                def attach() -> None:
                    done = 0
                    for sp, tick, choice in picks:
                        if not tick.value or choice.value is None:
                            continue
                        try:
                            splits.attach_pending(sp.txn_id, choice.value)
                            done += 1
                        except splits.SplitError as exc:
                            ui.notify(f"{sp.name}: {exc}", type="warning")
                    if done:
                        ui.notify(f"Attached {done} split{'s' if done != 1 else ''}", type="positive")
                    dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Not now", on_click=dlg.close).props("flat")
                    ui.button("Attach", icon="link", on_click=attach)
            dlg.on("hide", dlg.delete)
            dlg.open()

        @ui.refreshable
        def overview() -> None:
            with ui.column().classes("w-full gap-3"):
                account_statuses()
            with ui.card().classes("w-full gap-3"):
                import_history()

        overview()

        def redraw_preview() -> None:
            if state.get("preview") and state.get("grid_box"):  # a statement being previewed
                render_grid()
                update_summary()

        on_hide_change(sent, overview, redraw_preview)

        ui.label("Tip: the generic parsers handle most Excel exports. Share a sample PDF per bank/card "
                 "to get a dedicated parser. Password-protected files will ask for the password.") \
            .classes("text-xs et-faint")
