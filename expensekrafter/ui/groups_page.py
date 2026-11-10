"""Expense groups: make a group for a trip or occasion, fill it from Transactions
(or with card expenses not imported yet), and split or reimburse it as a whole."""

from __future__ import annotations

from datetime import date

from nicegui import app, ui

from ..db.models import AccountType, Direction
from ..services import groups, ledger
from ..services import money
from ..services.money import fmt, to_minor
from .layout import frame


def _rupees(minor: int) -> str:
    if money.hidden():
        return money.masked()
    return f"₹{minor / 100:,.0f}" if minor % 100 == 0 else fmt(minor)


def _span(g: groups.Group) -> str:
    if not g.first_date:
        return f"made {g.created:%d %b %Y}"
    if g.first_date == g.last_date:
        return f"{g.first_date:%d %b %Y}"
    same_year = g.first_date.year == g.last_date.year
    return f"{g.first_date:%d %b}{'' if same_year else f' {g.first_date:%Y}'} – {g.last_date:%d %b %Y}"


@ui.page("/groups")
def groups_page() -> None:
    with frame("Expense groups", "A trip, an event or one purchase paid in parts: keep its transactions together, "
                                 "and split or claim it as a whole"):
        store = app.storage.user

        with ui.row().classes("w-full items-center gap-4"):
            ui.label("Add transactions from Transactions: tick them, then Add to group.").classes("et-muted text-sm")
            ui.space()
            ui.button("New group", icon="create_new_folder", on_click=lambda: group_dialog(None))

        def act(fn, *args, done: str = "", **kwargs) -> bool:
            try:
                fn(*args, **kwargs)
            except groups.GroupError as exc:
                ui.notify(str(exc), type="warning")
                return False
            if done:
                ui.notify(done, type="positive")
            listing.refresh()
            return True

        @ui.refreshable
        def listing() -> None:
            all_groups = groups.groups()
            if not all_groups:
                with ui.column().classes("w-full items-center py-16 gap-2"):
                    ui.icon("folder_special").classes("text-5xl et-faint")
                    ui.label("No groups yet. Click New group, e.g. “Vietnam trip, Nov 2026”, then add its "
                             "transactions from Transactions.").classes("et-muted")
                return
            opened = set(store.get("groups_open", [all_groups[0].id]))
            for g in all_groups:
                group_card(g, g.id in opened)

        def set_open(group_id: int, value: bool) -> None:
            opened = set(store.get("groups_open", []))
            (opened.add if value else opened.discard)(group_id)
            store["groups_open"] = sorted(opened)

        def group_card(g: groups.Group, is_open: bool) -> None:
            exp = ui.expansion(value=is_open, on_value_change=lambda e, gid=g.id: set_open(gid, e.value)) \
                .classes("w-full et-month et-rise")
            with exp.add_slot("header"):
                with ui.row().classes("w-full items-center gap-4 no-wrap"):
                    ui.icon("folder_special").classes("text-2xl flex-none").style(f"color: {g.color or 'var(--text-3)'}")
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(g.name).classes("et-display text-lg font-semibold truncate")
                        facts = [f"{g.count} transaction{'s' if g.count != 1 else ''}", _span(g)]
                        ui.label(" · ".join(facts + ([g.label] if g.label else []))).classes("text-xs et-faint")
                    if g.waiting:
                        ui.label(f"{g.waiting} waiting for a statement").classes("et-chip flex-none")
                    ui.space()
                    if sp := g.split:
                        # A group someone pays you back for: the claim at a glance.
                        title = "Expense report" if len(sp.people) == 1 else "Split"
                        with ui.column().classes("gap-0 items-end flex-none"):
                            ui.label(f"{title} · {_rupees(sp.owed_minor)} claimed").classes("text-xs font-medium")
                            ui.label(f"{_rupees(sp.paid_minor)} received" +
                                     (f" · {_rupees(sp.remaining_minor)} to come" if sp.remaining_minor else " ✓")) \
                                .classes("text-xs " + ("et-warn" if sp.remaining_minor else "et-pos"))
                    ui.label(_rupees(g.total_minor)).classes("et-num font-semibold w-28 text-right flex-none")
            with exp, ui.column().classes("w-full gap-0 px-4 pb-3"):
                if g.notes:
                    ui.label(f"“{g.notes}”").classes("text-xs et-muted italic pb-2")
                if not g.items:
                    ui.label("Nothing in it yet. Tick transactions on Transactions → Add to group, or add a card "
                             "expense that isn't imported yet.").classes("text-sm et-faint py-2")
                for item in g.items:
                    with ui.row().classes("w-full items-center gap-3 no-wrap py-1 et-hero-rule"):
                        ui.label(f"{item.txn_date:%d %b %Y}").classes("text-sm et-faint w-24 flex-none")
                        ui.label(item.name).classes("text-sm flex-1 truncate").tooltip(item.description)
                        if item.expected:
                            ui.label("waiting for statement").classes("et-chip flex-none")
                        ui.label(item.account).classes("text-xs et-faint flex-none")
                        sign = "−" if item.direction == Direction.debit else "+"
                        ui.label(f"{sign}{fmt(item.amount_minor)}").classes(
                            "et-num text-sm w-28 text-right flex-none" + ("" if sign == "−" else " et-pos"))
                        ui.button(icon="close", on_click=lambda i=item: act(groups.remove_from_group, [i.txn_id],
                                                                            done="Removed from the group")) \
                            .props("flat round dense size=sm").tooltip(
                                "Remove (deletes this expected expense)" if item.expected else "Remove from group")
                with ui.row().classes("w-full gap-2 pt-3"):
                    if g.split:
                        ui.button("Open split / reimbursement", icon="call_split",
                                  on_click=lambda: ui.navigate.to("/splits")).props("outline dense no-caps")
                    else:
                        ui.button("Split / reimburse this group", icon="call_split",
                                  on_click=lambda gid=g.id: ui.navigate.to(f"/splits?new=g:{gid}")) \
                            .props("dense no-caps")
                    ui.button("Card expense not imported yet", icon="schedule",
                              on_click=lambda g=g: expected_dialog(g)).props("outline dense no-caps")
                    ui.space()
                    ui.button(icon="edit", on_click=lambda g=g: group_dialog(g)).props("flat round dense") \
                        .tooltip("Rename, label, notes")
                    ui.button(icon="delete", on_click=lambda g=g: confirm_delete(g)) \
                        .props("flat round dense color=negative").tooltip("Delete group")

        def group_dialog(g: groups.Group | None) -> None:
            labels = {lab.id: lab.name for lab in ledger.labels()}
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[30rem] max-w-full gap-3"):
                ui.label("Edit group" if g else "New expense group").classes("et-card-title")
                name = ui.input("Name", value=g.name if g else "", placeholder="e.g. Vietnam trip, Nov 2026") \
                    .props("outlined dense autofocus").classes("w-full")
                label = ui.select(labels, label="Label (optional)", value=g.label_id if g else None,
                                  with_input=True, clearable=True).props("outlined dense options-dense") \
                    .classes("w-full")
                notes = ui.input("Notes (optional)", value=g.notes if g else "",
                                 placeholder="e.g. claim ref EXP-1042").props("outlined dense").classes("w-full")

                def save() -> None:
                    ok = (act(groups.update_group, g.id, name=name.value, label_id=label.value, notes=notes.value or "",
                              done="Saved") if g else
                          act(groups.create_group, name.value, label.value, notes.value or "", done="Group created"))
                    if ok:
                        dlg.close()

                name.on("keydown.enter", save)
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save" if g else "Create", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def expected_dialog(g: groups.Group) -> None:
            accounts = ledger.accounts()
            cards = [a for a in accounts if a.type == AccountType.credit_card]
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full gap-3"):
                ui.label(f"Card expense for “{g.name}”").classes("et-card-title")
                ui.label("For a payment whose statement isn't imported yet. It counts in the group now and is "
                         "matched to the real transaction when you import that statement.").classes("text-sm et-muted")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    account = ui.select({a.id: a.name for a in accounts}, label="Paid with",
                                        value=(cards or accounts)[0].id if accounts else None) \
                        .props("outlined dense options-dense").classes("flex-1")
                    day = ui.input("Date", value=date.today().isoformat()).props("outlined dense type=date") \
                        .classes("w-40")
                    amount = ui.number("Amount ₹", min=0, format="%.2f").props("outlined dense stack-label") \
                        .classes("w-36")
                what = ui.input("What for", placeholder="e.g. Dinner in Hanoi").props("outlined dense") \
                    .classes("w-full")

                def save() -> None:
                    try:
                        when = date.fromisoformat(day.value)
                    except (TypeError, ValueError):
                        ui.notify("Pick the date", type="warning")
                        return
                    if act(groups.add_expected, g.id, account.value, when, to_minor(amount.value or 0),
                           what.value or "", g.label_id, done="Added; it'll be matched when the statement comes in"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Add", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def confirm_delete(g: groups.Group) -> None:
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label(f"Delete “{g.name}”?").classes("font-medium")
                ui.label("Its transactions stay, just ungrouped" +
                         (", and its card expenses not imported yet are dropped." if g.waiting else ".")) \
                    .classes("text-sm et-muted")
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Delete", icon="delete",
                              on_click=lambda: act(groups.delete_group, g.id, done="Group deleted") and dlg.close()) \
                        .props("color=negative")
            dlg.on("hide", dlg.delete)
            dlg.open()

        with ui.column().classes("w-full gap-3"):
            listing()
        dialog_host = ui.element("div")
