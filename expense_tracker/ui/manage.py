"""Accounts, labels, settings and the (upcoming) splits page."""

from __future__ import annotations

from html import escape

from nicegui import ui
from sqlmodel import select

from ..config import save_settings, settings
from ..db import get_session
from ..db.models import Account, AccountType, Frequency, Label
from ..importers import parser_options
from ..services import vault
from ..services.accounts import delete_account, mask_account_number, save_account, usage_count
from .layout import frame


@ui.page("/accounts")
def accounts_page() -> None:
    with frame("Accounts", "Bank accounts and credit cards you import from"):
        with ui.row().classes("w-full justify-end"):
            ui.button("Add account", icon="add", on_click=lambda: account_dialog(None))

        @ui.refreshable
        def listing() -> None:
            with get_session() as s:
                rows = s.exec(select(Account).order_by(Account.type, Account.name)).all()
            if not rows:
                ui.label("No accounts yet.").classes("et-muted")
                return
            formats = parser_options()
            for acc in rows:
                is_card = acc.type == AccountType.credit_card
                with ui.card().classes("w-full et-card-hover et-rise" + ("" if acc.active else " opacity-60")):
                    with ui.row().classes("w-full items-center gap-4"):
                        grad = ("linear-gradient(135deg,#3ccfe8,#2a9d8f)" if is_card
                                else "linear-gradient(135deg,#8b7cff,#5b8cff)")
                        with ui.element("div").classes("et-avatar").style(f"background:{grad};width:44px;height:44px"):
                            ui.icon("credit_card" if is_card else "account_balance").classes("text-xl")
                        with ui.column().classes("gap-0 min-w-56"):
                            ui.label(acc.name).classes("font-semibold")
                            ui.label(" · ".join(filter(None, [acc.institution, acc.number_masked]))) \
                                .classes("text-xs et-muted")
                        ui.label(formats.get(acc.parser_key, acc.parser_key)).classes("text-sm w-56")
                        if acc.type == AccountType.credit_card:
                            ui.label(f"Statement day {acc.statement_day or '-'} · due day {acc.due_day or '-'}") \
                                .classes("text-sm")
                        if not acc.active:
                            ui.badge("inactive", color="grey")
                        ui.space()
                        if vault.has_password(acc.id):
                            ui.button("Forget password", icon="key_off",
                                      on_click=lambda acc=acc: (vault.forget_password(acc.id), listing.refresh())) \
                                .props("flat dense no-caps").tooltip("Remove the saved statement password")
                        ui.button(icon="edit", on_click=lambda acc=acc: account_dialog(acc)) \
                            .props("flat round dense").tooltip("Edit")
                        ui.button(icon="delete", on_click=lambda acc=acc: confirm_delete(acc)) \
                            .props("flat round dense color=negative").tooltip("Delete")

        def account_dialog(acc: Account | None) -> None:
            editing = acc is not None
            acc = acc or Account(name="", type=AccountType.bank, parser_key="generic_excel")
            with ui.dialog() as dlg, ui.card().classes("w-[30rem]"):
                ui.label("Edit account" if editing else "Add account").classes("text-lg font-medium")
                name = ui.input("Name", value=acc.name, placeholder="HDFC Savings").classes("w-full")
                with ui.row().classes("w-full gap-3"):
                    kind = ui.select({t.value: t.value.replace("_", " ") for t in AccountType},
                                     value=acc.type.value, label="Type").classes("w-36")
                    inst = ui.input("Bank / issuer", value=acc.institution).classes("flex-1")
                keep_hint = f"Leave blank to keep {acc.number_masked}" if editing and acc.number_masked else ""
                number = ui.input("New account / card number" if editing else "Account / card number",
                                  placeholder=keep_hint).classes("w-full").props("autocomplete=off")
                masked_preview = ui.label().classes("text-xs et-muted")

                def update_preview() -> None:
                    try:
                        masked = mask_account_number(number.value)
                        masked_preview.text = f"Stored as {masked}" if masked else ""
                    except ValueError as exc:
                        masked_preview.text = str(exc)

                number.on_value_change(update_preview)
                parser = ui.select(parser_options(), value=acc.parser_key, label="Statement format") \
                    .classes("w-full")
                with ui.row().classes("w-full gap-3") as card_fields:
                    st_day = ui.number("Statement day", value=acc.statement_day, min=1, max=31).classes("flex-1")
                    due_day = ui.number("Due day", value=acc.due_day, min=1, max=31).classes("flex-1")
                card_fields.bind_visibility_from(kind, "value", value="credit_card")
                active = ui.switch("Active (shown in imports and filters)", value=acc.active)

                def save() -> None:
                    if not name.value:
                        ui.notify("Name is required", type="warning")
                        return
                    is_card = kind.value == AccountType.credit_card.value
                    acc.name, acc.type, acc.institution = name.value, AccountType(kind.value), inst.value
                    acc.parser_key, acc.active = parser.value, active.value
                    acc.statement_day = int(st_day.value) if is_card and st_day.value else None
                    acc.due_day = int(due_day.value) if is_card and due_day.value else None
                    try:
                        save_account(acc, number.value)
                    except ValueError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    number.value = ""  # don't keep the full number around in the UI
                    dlg.close()
                    listing.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.open()

        def confirm_delete(acc: Account) -> None:
            used = usage_count(acc.id)
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                if used:
                    ui.label(f"{acc.name} has {used} transactions/statements.").classes("font-medium")
                    ui.label("Deleting it would orphan that data. Mark it inactive instead "
                             "(Edit → Active) to hide it from imports and filters.").classes("text-sm")
                    with ui.row().classes("w-full justify-end"):
                        ui.button("OK", on_click=dlg.close).props("flat")
                else:
                    ui.label(f"Delete {acc.name}?").classes("font-medium")
                    ui.label("Its saved statement password is removed too.").classes("text-sm et-muted")

                    def do_delete() -> None:
                        delete_account(acc.id)
                        dlg.close()
                        listing.refresh()

                    with ui.row().classes("w-full justify-end"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")
                        ui.button("Delete", icon="delete", on_click=do_delete).props("color=negative")
            dlg.open()

        listing()


@ui.page("/labels")
def labels_page() -> None:
    with frame("Labels", "Categories for your spending; recurring ones power the subscriptions view"):
        @ui.refreshable
        def listing() -> None:
            with get_session() as s:
                rows = s.exec(select(Label).order_by(Label.name)).all()
            with ui.row().classes("gap-2 flex-wrap"):
                for lab in rows:
                    recurring = (f'<span class="et-faint">↻ {lab.frequency.value}</span>'
                                 if lab.is_recurring and lab.frequency else "")
                    ui.html(
                        f'<span class="et-chip" style="padding:6px 14px;font-size:13px;color:var(--text);'
                        f'background:{lab.color}1f;border-color:{lab.color}55">'
                        f'<span class="et-dot" style="background:{lab.color}"></span>{escape(lab.name)}'
                        f'{recurring}</span>', sanitize=False)

        with ui.card().classes("w-full"):
            ui.label("Add label").classes("font-medium")
            with ui.row().classes("items-end gap-3"):
                name = ui.input("Name").classes("w-48")
                color = ui.color_input("Color", value="#4c78a8").classes("w-40")
                recurring = ui.checkbox("Recurring")
                freq = ui.select({f.value: f.value for f in Frequency}, value="monthly", label="Frequency").classes("w-36")
                freq.bind_visibility_from(recurring, "value")

                def add() -> None:
                    if not name.value:
                        return
                    with get_session() as s:
                        s.add(Label(name=name.value, color=color.value, is_recurring=recurring.value,
                                    frequency=Frequency(freq.value) if recurring.value else None))
                        s.commit()
                    name.value = ""
                    listing.refresh()

                ui.button("Add", icon="add", on_click=add)
        listing()


@ui.page("/splits")
def splits_page() -> None:
    with frame("Splits", "Shared expenses and the money friends paid back"):
        ui.label("Link money paid back by friends (bank credits) to the expense it covers.")
        ui.label("Coming next: pick an expense or group, then attach the reimbursement credits.").classes("et-muted")


@ui.page("/settings")
def settings_page() -> None:
    with frame("Settings", "Where your data lives and when to remind you"):
        with ui.card().classes("w-full max-w-3xl"):
            ui.label("Data folder").classes("font-medium")
            ui.label("Point this at a folder inside Google Drive for Desktop (e.g. G:\\My Drive\\ExpenseTracker) "
                     "to sync the database. Open the app on one machine at a time.").classes("text-sm et-muted")
            data_dir = ui.input("Folder", value=settings.data_dir).classes("w-full")
            alert_days = ui.number("Alert when a card bill is due within (days)", value=settings.due_alert_days,
                                   min=0, max=30).classes("w-80")

            def save() -> None:
                restart = data_dir.value != settings.data_dir
                settings.data_dir = data_dir.value
                settings.due_alert_days = int(alert_days.value or 0)
                save_settings(settings)
                ui.notify("Saved" + (". Restart the app to use the new data folder." if restart else ""),
                          type="positive")

            ui.button("Save", on_click=save)
