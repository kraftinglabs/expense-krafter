"""Accounts, labels and settings."""

from __future__ import annotations

import os

from nicegui import ui
from sqlmodel import select

from ..config import save_settings, settings
from ..db import get_session
from ..db.models import Account, AccountType, IntervalUnit, Label
from ..importers import parser_options
from ..services import app_lock, labels, narration, vault
from ..services.importer import reapply_rule_types
from ..services.money import fmt
from ..services.accounts import delete_account, mask_account_number, save_account, usage_count
from . import auth, icons
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


CUSTOM = "Custom…"
UNITS = {u.value: f"{u.value}s" for u in IntervalUnit}


def interval_picker(every: int | None = 1, unit: IntervalUnit | None = IntervalUnit.month):
    """A preset select (Weekly ... Yearly) plus 'Custom…' for every N weeks /
    months / years. Returns a function giving the chosen (every, unit)."""
    current = (every or 1, IntervalUnit(unit or IntervalUnit.month))
    name = labels.interval_name(*current)
    with ui.row().classes("items-end gap-2 no-wrap") as row:
        preset = ui.select(list(labels.PRESETS) + [CUSTOM], value=name if name in labels.PRESETS else CUSTOM)             .props("outlined dense").classes("w-40")
        with ui.row().classes("items-end gap-2 no-wrap") as custom:
            ui.label("every").classes("et-muted text-sm pb-2")
            count = ui.number(value=current[0], min=1, max=520, step=1, format="%d")                 .props("outlined dense").classes("w-20")
            unit_sel = ui.select(UNITS, value=current[1].value).props("outlined dense").classes("w-28")
        custom.bind_visibility_from(preset, "value", backward=lambda v: v == CUSTOM)

    def value() -> tuple[int, IntervalUnit]:
        if preset.value != CUSTOM:
            return labels.PRESETS[preset.value]
        return int(count.value or 1), IntervalUnit(unit_sel.value)

    value.row = row
    return value


NO_GROUP = 0  # "no group" in the group select (None can't be told apart from "nothing picked")


def _group_options(exclude: int | None = None) -> dict[int, str]:
    """Top-level labels a label can be grouped under."""
    with get_session() as s:
        tops = s.exec(select(Label).where(Label.parent_id == None).order_by(Label.name)).all()  # noqa: E711
    return {NO_GROUP: "No group"} | {lab.id: lab.name for lab in tops if lab.id != exclude}


@ui.page("/labels")
def labels_page() -> None:
    with frame("Labels", "Categories for your spending; recurring ones power the subscriptions view. "
                         "Labels in a group count as the group on the dashboard"):

        with ui.card().classes("w-full"):
            ui.label("Add label").classes("et-card-title")
            ui.label("A colour is picked for you; change it any time with Edit.").classes("text-xs et-faint")
            with ui.row().classes("items-end gap-3 mt-2"):
                name = ui.input("Name").classes("w-56").props("outlined dense")
                group = ui.select(_group_options(), value=NO_GROUP, label="Group under") \
                    .props("outlined dense options-dense").classes("w-48")
                recurring = ui.checkbox("Recurring")
                interval = interval_picker()
                interval.row.bind_visibility_from(recurring, "value")

                def add() -> None:
                    try:
                        lab = labels.create_label(name.value, recurring.value, *interval(),
                                                  parent_id=group.value or None)
                    except ValueError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    name.value = ""
                    group.set_options(_group_options(), value=NO_GROUP)
                    listing.refresh()
                    ui.notify(f"Added “{lab.name}”", type="positive")

                name.on("keydown.enter", add)
                ui.button("Add", icon="add", on_click=add)

        @ui.refreshable
        def listing() -> None:
            with get_session() as s:
                everything = s.exec(select(Label).order_by(Label.name)).all()
            # Each group label followed by the labels inside it.
            rows = []
            for top in (lab for lab in everything if lab.parent_id is None):
                rows += [top] + [lab for lab in everything if lab.parent_id == top.id]
            if not rows:
                ui.label("No labels yet.").classes("et-muted")
                return
            with ui.element("div").classes("et-card w-full"):
                for i, lab in enumerate(rows):
                    used = labels.usage_count(lab.id)
                    with ui.row().classes("w-full items-center gap-4 no-wrap px-5 py-3") \
                            .style("" if i == 0 else "border-top: var(--hair) solid var(--border)"):
                        if lab.parent_id:
                            ui.icon("subdirectory_arrow_right").classes("et-faint flex-none ml-2")
                        ui.html(f'<span class="et-dot" style="width:12px;height:12px;background:{lab.color}"></span>',
                                sanitize=False).classes("flex-none")
                        ui.label(lab.name).classes("flex-1 min-w-0 et-break")
                        if (inside := sum(1 for x in everything if x.parent_id == lab.id)):
                            ui.label(f"Group of {inside}").classes("et-chip accent flex-none")
                        if lab.is_recurring:
                            ui.label(f"Recurring · {labels.interval_name(lab.every, lab.unit).lower()}")                                 .classes("et-chip flex-none")
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
                holds = labels.with_children({lab.id}) - {lab.id}
                e_group = ui.select(_group_options(exclude=lab.id), value=lab.parent_id or NO_GROUP,
                                    label="Group under").props("outlined dense options-dense").classes("w-full")
                if holds:
                    e_group.disable()
                    ui.label("It's a group for other labels, so it can't go into a group itself.") \
                        .classes("text-xs et-faint -mt-2")
                e_rec = ui.checkbox("Recurring", value=lab.is_recurring)
                e_interval = interval_picker(lab.every, lab.unit)
                e_interval.row.bind_visibility_from(e_rec, "value")

                def save() -> None:
                    every, unit = e_interval()
                    try:
                        labels.update_label(lab.id, name=e_name.value, color=e_color.value or lab.color,
                                            recurring=e_rec.value, every=every, unit=unit,
                                            parent_id=e_group.value or None)
                    except ValueError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    dlg.close()
                    listing.refresh()
                    group.set_options(_group_options(), value=NO_GROUP)

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.open()

        def confirm_delete(lab: Label) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label(f"Delete “{lab.name}”?").classes("text-lg")
                ui.label("It isn't used by any transaction."
                         + (" Labels in its group stay, ungrouped." if labels.with_children({lab.id}) - {lab.id}
                            else "")).classes("text-sm et-muted")

                def do_delete() -> None:
                    try:
                        labels.delete_label(lab.id)
                    except ValueError as exc:  # became used meanwhile
                        ui.notify(str(exc), type="warning")
                    dlg.close()
                    listing.refresh()
                    group.set_options(_group_options(), value=NO_GROUP)

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Delete", icon="delete", on_click=do_delete).props("color=negative")
            dlg.open()

        listing()


def app_password_card() -> None:
    is_set = app_lock.is_set()
    with ui.card().classes("w-full max-w-3xl"):
        ui.label("App password").classes("font-medium")
        ui.label("Asked for every time ExpenseKrafter opens; Sign out in the sidebar locks it again. "
                 "It's stored only as a hash, with your data. It locks the app, not the database file "
                 "itself.").classes("text-sm et-muted")
        if auth.env_password_active():
            ui.label(f"A password from {auth.PASSWORD_ENV} is also accepted on this run.").classes("text-xs et-faint")

        def field(label: str):
            return ui.input(label, password=True, password_toggle_button=True) \
                .props("outlined dense autocomplete=new-password").classes("w-64")

        with ui.row().classes("items-end gap-3 flex-wrap"):
            current = field("Current password") if is_set else None
            new = field("New password")
            confirm = field("Repeat new password")

        def done(message: str) -> None:
            auth.refresh()
            auth.mark_signed_in()  # don't lock out the person who just changed it
            ui.notify(message, type="positive")
            # Reload so the sidebar's Sign out (shown only with a password) updates too.
            ui.timer(1.2, ui.navigate.reload, once=True)

        def save() -> None:
            try:
                app_lock.set_password(new.value or "", confirm.value or "", current.value if current else None)
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            done("Password changed" if is_set else "Password set: you'll be asked for it next time")

        def remove() -> None:
            try:
                app_lock.remove_password(current.value or "")
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            done("Password removed")

        with ui.row().classes("gap-2"):
            ui.button("Change password" if is_set else "Set password", icon="lock", on_click=save)
            if is_set:
                ui.button("Remove password", icon="lock_open", on_click=remove).props("flat color=negative")


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

        app_password_card()

        with ui.card().classes("w-full max-w-3xl"):
            ui.label("Data folder").classes("font-medium")
            ui.label("Point this at a synced folder (e.g. Google Drive for Desktop, G:\\My Drive\\ExpenseKrafter, "
                     "or an Insync / rclone folder on Linux) to sync the database. Open the app on one machine "
                     "at a time.").classes("text-sm et-muted")
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
