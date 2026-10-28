"""Accounts, labels, settings and the (upcoming) splits page."""

from __future__ import annotations

import os

from nicegui import ui
from sqlmodel import select

from ..config import save_settings, settings
from ..db import get_session
from ..db.models import Account, AccountType, Frequency, Label
from ..importers import parser_options
from ..services import labels, narration, vault
from ..services.importer import reapply_rule_types
from ..services.money import fmt
from ..services.accounts import delete_account, mask_account_number, save_account, usage_count
from . import icons
from .layout import frame, set_theme


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
                with ui.card().classes("w-full et-card-hover et-rise" + ("" if acc.active else " opacity-60")):
                    with ui.row().classes("w-full items-center gap-4 no-wrap"):
                        ui.html(icons.tile_html(acc, 44), sanitize=False).classes("flex-none")
                        # Text side can shrink and wrap; the buttons on the right never move.
                        with ui.column().classes("gap-1 flex-1 min-w-0"):
                            ui.label(acc.name).classes("font-semibold break-words")
                            with ui.row().classes("gap-1.5 items-center flex-wrap"):
                                for bubble in filter(None, [
                                        acc.institution, acc.number_masked, formats.get(acc.parser_key, acc.parser_key),
                                        f"Statement day {acc.statement_day or '–'} · due day {acc.due_day or '–'}"
                                        if acc.type == AccountType.credit_card else ""]):
                                    ui.label(bubble).classes("et-chip")
                                if not acc.active:
                                    ui.label("inactive").classes("et-chip warn")
                        with ui.row().classes("flex-none items-center gap-1 no-wrap"):
                            if vault.has_password(acc.id):
                                ui.button("Forget password", icon="key_off",
                                          on_click=lambda acc=acc: (vault.forget_password(acc.id),
                                                                    listing.refresh())) \
                                    .props("flat dense no-caps").tooltip("Remove the saved statement password")
                            ui.button(icon="edit", on_click=lambda acc=acc: account_dialog(acc)) \
                                .props("flat round dense color=grey-5").tooltip("Edit")
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
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    icon_preview = ui.html("", sanitize=False).classes("flex-none")
                    icon = ui.select(icons.options(), value=acc.icon if acc.icon in icons.options() else "",
                                     label="Icon").classes("flex-1")

                def update_icon_preview() -> None:
                    probe = Account(name=name.value or "", institution=inst.value or "",
                                    type=AccountType(kind.value), icon=icon.value or "")
                    icon_preview.content = icons.tile_html(probe, 40)

                for field in (icon, name, inst, kind):
                    field.on_value_change(update_icon_preview)
                update_icon_preview()
                ui.label(f"Add your own: put square SVG/PNG logos in {icons.USER_DIR}") \
                    .classes("text-xs et-faint -mt-2 break-all")
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
                    acc.parser_key, acc.active, acc.icon = parser.value, active.value, icon.value or ""
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
        frequencies = {f.value: f.value.capitalize() for f in Frequency}

        with ui.card().classes("w-full"):
            ui.label("Add label").classes("et-card-title")
            ui.label("A colour is picked for you; change it any time with Edit.").classes("text-xs et-faint")
            with ui.row().classes("items-end gap-3 mt-2"):
                name = ui.input("Name").classes("w-56").props("outlined dense")
                recurring = ui.checkbox("Recurring")
                freq = ui.select(frequencies, value="monthly").props("outlined dense").classes("w-36")
                freq.bind_visibility_from(recurring, "value")

                def add() -> None:
                    try:
                        lab = labels.create_label(name.value, recurring.value,
                                                  Frequency(freq.value) if recurring.value else None)
                    except ValueError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    name.value = ""
                    listing.refresh()
                    ui.notify(f"Added “{lab.name}”", type="positive")

                name.on("keydown.enter", add)
                ui.button("Add", icon="add", on_click=add)

        @ui.refreshable
        def listing() -> None:
            with get_session() as s:
                rows = s.exec(select(Label).order_by(Label.name)).all()
            if not rows:
                ui.label("No labels yet.").classes("et-muted")
                return
            with ui.element("div").classes("et-card w-full"):
                for i, lab in enumerate(rows):
                    used = labels.usage_count(lab.id)
                    with ui.row().classes("w-full items-center gap-4 no-wrap px-5 py-3") \
                            .style("" if i == 0 else "border-top: var(--hair) solid var(--border)"):
                        ui.html(f'<span class="et-dot" style="width:12px;height:12px;background:{lab.color}"></span>',
                                sanitize=False).classes("flex-none")
                        ui.label(lab.name).classes("flex-1 min-w-0 et-break")
                        if lab.is_recurring and lab.frequency:
                            ui.label(f"Recurring · {lab.frequency.value}").classes("et-chip flex-none")
                        ui.label(f"{used} transaction{'s' if used != 1 else ''}" if used else "Unused") \
                            .classes("text-sm et-faint et-num w-28 text-right flex-none")
                        with ui.row().classes("flex-none gap-1 no-wrap"):
                            ui.button(icon="edit", on_click=lambda lab=lab: edit_dialog(lab)) \
                                .props("flat round dense color=grey-7").tooltip("Edit")
                            # A disabled button shows no tooltip, so the reason goes on a wrapper.
                            with ui.element("span") as wrap:
                                delete = ui.button(icon="delete", on_click=lambda lab=lab: confirm_delete(lab)) \
                                    .props("flat round dense color=negative")
                            if used:
                                delete.disable()
                                wrap.tooltip(f"In use by {used} transaction{'s' if used != 1 else ''}; can't delete")
                            else:
                                delete.tooltip("Delete")

        def edit_dialog(lab: Label) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-[26rem]"):
                ui.label("Edit label").classes("text-lg")
                e_name = ui.input("Name", value=lab.name).classes("w-full").props("outlined dense")
                e_color = ui.color_input("Colour", value=lab.color, preview=True).classes("w-full") \
                    .props("outlined dense")
                with ui.row().classes("gap-1.5 flex-wrap"):
                    for c in labels.PALETTE:
                        ui.element("div").classes("cursor-pointer").style(
                            f"width:22px;height:22px;border-radius:3px;background:{c}") \
                            .on("click", lambda c=c: e_color.set_value(c)).tooltip(c)
                e_rec = ui.checkbox("Recurring", value=lab.is_recurring)
                e_freq = ui.select(frequencies, value=(lab.frequency or Frequency.monthly).value) \
                    .props("outlined dense").classes("w-40")
                e_freq.bind_visibility_from(e_rec, "value")

                def save() -> None:
                    try:
                        labels.update_label(lab.id, name=e_name.value, color=e_color.value or lab.color,
                                            recurring=e_rec.value,
                                            frequency=Frequency(e_freq.value) if e_rec.value else None)
                    except ValueError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    dlg.close()
                    listing.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.open()

        def confirm_delete(lab: Label) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label(f"Delete “{lab.name}”?").classes("text-lg")
                ui.label("It isn't used by any transaction.").classes("text-sm et-muted")

                def do_delete() -> None:
                    try:
                        labels.delete_label(lab.id)
                    except ValueError as exc:  # became used meanwhile
                        ui.notify(str(exc), type="warning")
                    dlg.close()
                    listing.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Delete", icon="delete", on_click=do_delete).props("color=negative")
            dlg.open()

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
            ui.label("Appearance").classes("font-medium")
            with ui.row().classes("items-center gap-4"):
                ui.label("Theme").classes("text-sm et-muted w-20")
                ui.toggle({"light": "Light", "dark": "Dark"}, value=settings.theme,
                          on_change=lambda e: set_theme(e.value)) \
                    .props("unelevated no-caps toggle-color=primary color=transparent text-color=grey-7 "
                           "padding='4px 16px'").classes("et-segment")

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

        description_rules_card()


def reapply_dialog() -> None:
    """Preview, then apply, the rules' types on existing transactions."""
    changes = reapply_rule_types(apply=False)
    with ui.dialog() as dlg, ui.card().classes("w-[40rem]"):
        ui.label("Re-apply types from rules").classes("text-lg font-medium")
        ui.label("Only rows still at their default type change. Types you set yourself, or bills you linked, "
                 "are kept.").classes("text-sm et-muted")
        if not changes:
            ui.label("Everything already matches the rules.").classes("py-4")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=dlg.close).props("flat")
        else:
            with ui.column().classes("w-full gap-0 max-h-80 overflow-auto"):
                for c in changes:
                    with ui.row().classes("w-full items-center gap-3 no-wrap py-2") \
                            .style("border-top: 1px solid var(--border)"):
                        ui.label(f"{c.txn_date:%d %b %Y}").classes("text-xs et-faint w-20 flex-none")
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(narration.parse(c.description).display).classes("et-break text-sm")
                            ui.label(c.account).classes("text-xs et-faint")
                        ui.label(fmt(c.amount_minor)).classes("et-num text-sm flex-none")
                        ui.label(f"{c.old.value} → {c.new.value}").classes("et-chip accent flex-none")

            def apply() -> None:
                n = len(reapply_rule_types(apply=True))
                dlg.close()
                ui.notify(f"Updated {n} transaction{'s' if n != 1 else ''}", type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button(f"Update {len(changes)}", icon="check", on_click=apply)
    dlg.open()


def description_rules_card() -> None:
    with ui.card().classes("w-full max-w-3xl"):
        ui.label("Description rules").classes("font-medium")
        ui.label("Regex rules that turn raw bank text (like SBI's 'WDL TFR UPI/DR/…') into a clean name with "
                 "details, and set the type on import. Edit your copy in any text editor; changes apply on the "
                 "next page load.").classes("text-sm et-muted")

        @ui.refreshable
        def status() -> None:
            path = narration.rules_path()
            mine = path == narration.USER_RULES
            with ui.row().classes("items-center gap-2 no-wrap w-full"):
                ui.label("Your copy" if mine else "Built-in defaults").classes("et-chip accent" if mine else "et-chip")
                ui.label(str(path)).classes("text-xs et-faint et-break")
            if err := narration.rules_error():
                ui.label(f"Your rules file has an error, so the defaults are in use: {err}").classes("text-sm et-neg")
            with ui.row().classes("gap-2"):
                if not mine:
                    ui.button("Create my copy", icon="content_copy",
                              on_click=lambda: (narration.create_user_copy(), status.refresh(),
                                                ui.notify("Created. Edit it, then reload this page.",
                                                          type="positive")))
                ui.button("Open folder", icon="folder_open",
                          on_click=lambda: os.startfile(narration.rules_path().parent)).props("outline")
                ui.button("Re-apply types…", icon="published_with_changes", on_click=reapply_dialog) \
                    .props("outline").tooltip("Update existing transactions after changing rules")

        status()

        ui.label("Try a description").classes("text-sm font-medium mt-2")
        sample = ui.textarea(placeholder="Paste a description from a statement").classes("w-full") \
            .props("outlined autogrow")
        result = ui.column().classes("w-full gap-1")

        def test() -> None:
            result.clear()
            if not sample.value:
                return
            n = narration.parse(sample.value)
            with result:
                if n.parsed:
                    ui.label(n.title).classes("font-semibold")
                    with ui.row().classes("gap-1.5 flex-wrap"):
                        for chip in [n.method, *n.details, f"ref {n.ref}" if n.ref else ""]:
                            if chip:
                                ui.label(chip).classes("et-chip et-break")
                    ui.label(f"Matched rule: {n.rule}").classes("text-xs et-faint")
                else:
                    ui.label(n.text).classes("et-muted et-break")
                    ui.label("No rule matched: shown as plain text with bubbles.").classes("text-xs et-warn")
                if n.kind:
                    ui.label(f"Type on import: {n.kind}").classes("text-xs et-faint")

        sample.on_value_change(test)
