"""Transactions as a month → day accordion with inline label/type/notes editing."""

from __future__ import annotations

import re
from datetime import date
from html import escape
from itertools import groupby

from nicegui import ui

from ..db.models import Direction
from ..services import groups, ledger, narration
from ..services.analytics import merchant_name
from ..services.money import fmt
from . import icons
from .layout import frame, on_hide_change

KIND_LABELS = ledger.KIND_LABELS
DIRECTIONS = {None: "All", "debit": "Money out", "credit": "Money in"}


def _amount(minor: int, direction: Direction) -> tuple[str, str]:
    if direction == Direction.debit:
        return f"−{fmt(minor)}", ""
    return f"+{fmt(minor)}", "et-pos"


def _totals(debit: int, credit: int) -> None:
    """Out / in totals for a month or day header."""
    with ui.row().classes("gap-4 items-center no-wrap"):
        if credit:
            ui.label(f"+{fmt(credit)}").classes("et-num et-pos text-sm font-medium")
        if debit:
            ui.label(f"−{fmt(debit)}").classes("et-num text-sm font-medium")


def _avatar(description: str, color: str | None) -> None:
    name = merchant_name(description)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    bg = color or "#b4b9b6"
    ui.html(f'<div class="et-avatar" style="background:{bg}">{escape(initials)}</div>', sanitize=False)


@ui.page("/transactions")
def transactions_page() -> None:
    with frame("Transactions", "Label, group and annotate everything you've imported"):
        accounts = {a.id: a.name for a in ledger.accounts()}
        f = ledger.TxnFilter()
        state: dict = {"selected": set(), "open": set()}

        def apply() -> None:
            n = f.extra_count()
            filters_btn.text = f"Filters · {n}" if n else "Filters"
            filters_btn.props(f"color={'primary' if n else 'grey-8'}")
            months.refresh()

        # Search, filters and group/ungroup stay pinned at the top while the list scrolls.
        with ui.column().classes("w-full gap-3 et-sticky-bar"):
            with ui.row().classes("w-full items-center gap-3"):
                search = ui.input(placeholder="Search name, details, notes, label, amount…",
                                  on_change=lambda e: (setattr(f, "search", e.value or ""), apply())) \
                    .props("outlined dense clearable debounce=300").classes("w-80")
                with search.add_slot("prepend"):
                    ui.icon("search")
                ui.select({None: "All accounts"} | accounts, value=None,
                          on_change=lambda e: (setattr(f, "account_id", e.value), apply())) \
                    .props("outlined dense options-dense").classes("w-52")
                filters_btn = ui.button("Filters", icon="tune", on_click=lambda: panel.set_visibility(not panel.visible)) \
                    .props("outline color=grey-8")
                ui.switch("Unlabelled only", on_change=lambda e: (setattr(f, "unlabelled_only", e.value), apply())) \
                    .classes("et-muted")
                ui.space()
                selection_label = ui.label().classes("text-sm et-muted")
                group_btn = ui.button("Add to group", icon="create_new_folder").props("outline")
                with group_btn:
                    group_menu = ui.menu()
                group_btn.on("click", lambda: fill_group_menu())
                ungroup_btn = ui.button("Remove from group", icon="folder_off", on_click=lambda: ungroup()) \
                    .props("outline")

            with ui.card().classes("w-full gap-4") as panel:
                with ui.row().classes("w-full items-end gap-4 flex-wrap"):
                    kinds = ui.select({k.value: v for k, v in KIND_LABELS.items()}, multiple=True, label="Type",
                                      on_change=lambda e: (setattr(f, "kinds", set(e.value or [])), apply())) \
                        .props("outlined dense options-dense use-chips clearable").classes("w-60")
                    label_pick = ui.select({ledger.NO_LABEL: "No label"} | {lab.id: lab.name for lab in ledger.labels()},
                                           multiple=True, label="Label", with_input=True,
                                           on_change=lambda e: (setattr(f, "label_ids", set(e.value or [])), apply())) \
                        .props("outlined dense options-dense use-chips clearable").classes("w-72")
                    direction = ui.toggle(DIRECTIONS, value=None,
                                          on_change=lambda e: (setattr(f, "direction", e.value), apply())) \
                        .props("unelevated no-caps toggle-color=primary color=transparent text-color=grey-8 "
                               "padding='6px 12px'").classes("et-segment")
                with ui.row().classes("w-full items-end gap-4 flex-wrap"):
                    def on_date(attr: str, value: str | None) -> None:
                        setattr(f, attr, date.fromisoformat(value) if value else None)
                        apply()

                    date_from = ui.input("From", on_change=lambda e: on_date("date_from", e.value)) \
                        .props("outlined dense clearable type=date stack-label").classes("w-44")
                    date_to = ui.input("To", on_change=lambda e: on_date("date_to", e.value)) \
                        .props("outlined dense clearable type=date stack-label").classes("w-44")

                    def on_amount(attr: str, value) -> None:
                        setattr(f, attr, float(value) if value not in (None, "") else None)
                        apply()

                    amount_min = ui.number("Min amount ₹", min=0, on_change=lambda e: on_amount("amount_min", e.value)) \
                        .props("outlined dense clearable").classes("w-40")
                    amount_max = ui.number("Max amount ₹", min=0, on_change=lambda e: on_amount("amount_max", e.value)) \
                        .props("outlined dense clearable").classes("w-40")
                    ui.space()

                    def clear_filters() -> None:
                        for el in (kinds, label_pick):
                            el.set_value([])
                        for el in (date_from, date_to, amount_min, amount_max):
                            el.set_value(None)
                        direction.set_value(None)

                    ui.button("Clear filters", icon="filter_alt_off", on_click=clear_filters).props("flat")
            panel.set_visibility(False)

        def update_selection() -> None:
            n = len(state["selected"])
            selection_label.text = f"{n} selected" if n else ""
            # Only shown while rows are ticked, so the toolbar fits on one line.
            group_btn.set_visibility(n >= 1)
            ungroup_btn.set_visibility(n >= 1)

        def toggle(txn_id: int, on: bool) -> None:
            (state["selected"].add if on else state["selected"].discard)(txn_id)
            update_selection()

        labels = ledger.labels()
        label_options = {lab.id: lab.name for lab in labels}
        label_color = {lab.id: lab.color for lab in labels}

        all_accounts = {a.id: a for a in ledger.accounts(active_only=False)}

        def bubbles(t: ledger.TxnRow, n: narration.Narration) -> None:
            """Second line: parsed facts first, then account / group / notes."""
            with ui.row().classes("gap-1.5 items-center flex-wrap min-w-0"):
                if n.method:
                    ui.label(n.method).classes("et-chip font-semibold").style("color: var(--text)")
                for detail in n.details:
                    ui.label(detail).classes("et-chip et-break")
                if acc := all_accounts.get(t.account_id):
                    ui.html(f'<span class="et-chip">{icons.tile_html(acc, 14)}{escape(acc.name)}</span>',
                            sanitize=False)
                if t.group_name:
                    ui.link(t.group_name, "/groups").classes("et-chip accent no-underline") \
                        .tooltip("In this expense group: open Expense groups")
                if t.notes:
                    ui.label(f"“{t.notes}”").classes("text-xs et-muted italic et-break")

        def txn_row(t: ledger.TxnRow) -> None:
            n = narration.parse(t.description)
            with ui.row().classes("w-full items-center gap-4 et-row no-wrap"):
                ui.checkbox(value=t.id in state["selected"], on_change=lambda e, i=t.id: toggle(i, e.value)) \
                    .props("dense").classes("flex-none")
                _avatar(t.description, label_color.get(t.label_id))
                # Text column shrinks and wraps; the controls on the right keep their width.
                with ui.column().classes("gap-1 flex-1 min-w-0"):
                    ui.label(n.display).classes("et-break" + (" font-medium" if n.parsed else " text-sm et-muted")) \
                        .tooltip(re.sub(r"\s+", " ", t.description))
                    bubbles(t, n)

                with ui.row().classes("flex-none items-center gap-3 no-wrap"):
                    ui.button(icon="sticky_note_2" if t.notes else "edit_note",
                              on_click=lambda t=t: notes_dialog(t)) \
                        .props(f"flat round dense size=sm color={'primary' if t.notes else 'grey-7'}") \
                        .tooltip("Notes")
                    ui.select({k.value: v for k, v in KIND_LABELS.items()}, value=t.kind.value,
                              on_change=lambda e, i=t.id: ledger.set_fields(i, kind=e.value)) \
                        .props("dense outlined options-dense").classes("w-36")

                    def on_label(e, i=t.id) -> None:
                        ledger.set_fields(i, label_id=e.value)
                        e.sender.style(f"border-left: 4px solid {label_color.get(e.value, 'transparent')}")

                    ui.select(label_options, value=t.label_id, with_input=True, clearable=True,
                              label=None if t.label_id else "Label", on_change=on_label) \
                        .props("dense outlined options-dense").classes("w-48") \
                        .style(f"border-left: 4px solid {label_color.get(t.label_id, 'transparent')}")
                    text, color = _amount(t.amount_minor, t.direction)
                    ui.label(text).classes(f"w-28 text-right font-semibold et-num {color}")

        def month_body(month: str, container: ui.element) -> None:
            container.clear()
            txns = ledger.month_transactions(month, f)
            with container:
                for day, items in groupby(txns, key=lambda t: t.txn_date):
                    items = list(items)
                    debit = sum(t.amount_minor for t in items if t.direction == Direction.debit)
                    credit = sum(t.amount_minor for t in items if t.direction == Direction.credit)
                    with ui.expansion(value=True).classes("w-full et-day").props("dense") as day_exp:
                        with day_exp.add_slot("header"):
                            with ui.row().classes("w-full items-center gap-3"):
                                ui.label(f"{day:%a}").classes("et-eyebrow w-8")
                                ui.label(f"{day:%d %B}").classes("text-sm font-medium")
                                ui.label(f"{len(items)}").classes("et-chip")
                                ui.space()
                                _totals(debit, credit)
                        for t in items:
                            txn_row(t)

        @ui.refreshable
        def months() -> None:
            summaries = ledger.month_summaries(f)
            narrowed = f.is_narrowed()
            if narrowed:
                count = sum(ms.count for ms in summaries)
                with ui.row().classes("w-full items-center gap-3 px-1"):
                    ui.label(f"{count} transaction{'s' if count != 1 else ''} match").classes("text-sm font-medium")
                    _totals(sum(ms.debit_minor for ms in summaries), sum(ms.credit_minor for ms in summaries))
            if not summaries:
                with ui.column().classes("w-full items-center py-16 gap-2"):
                    ui.icon("search_off" if narrowed else "receipt_long").classes("text-5xl et-faint")
                    ui.label("Nothing matches. Try fewer words or clear some filters." if narrowed
                             else "No transactions yet.").classes("et-muted")
                return
            if not state["open"]:
                state["open"].add(summaries[0].month)  # newest month open by default
            for ms in summaries:
                # While searching or filtering, every matching month is shown open.
                is_open = narrowed or ms.month in state["open"]
                exp = ui.expansion(value=is_open).classes("w-full et-month et-rise")
                with exp.add_slot("header"):
                    with ui.row().classes("w-full items-center gap-4"):
                        month_start = date.fromisoformat(ms.month + "-01")
                        with ui.column().classes("gap-0"):
                            ui.label(month_start.strftime("%B")).classes("et-display text-lg font-semibold")
                            ui.label(f"{month_start:%Y} · {ms.count} transactions").classes("text-xs et-faint")
                        if ms.unlabelled:
                            ui.html(f'<span class="et-chip warn">{ms.unlabelled} unlabelled</span>',
                                    sanitize=False)
                        ui.space()
                        _totals(ms.debit_minor, ms.credit_minor)
                with exp:
                    body = ui.column().classes("w-full gap-0")
                if is_open:
                    month_body(ms.month, body)

                def on_toggle(e, month=ms.month, body=body) -> None:
                    if e.value:
                        state["open"].add(month)
                        if not body.default_slot.children:  # render lazily on first open
                            month_body(month, body)
                    else:
                        state["open"].discard(month)

                exp.on_value_change(on_toggle)

        def notes_dialog(t: ledger.TxnRow) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-[28rem]"):
                ui.label(t.description).classes("font-medium")
                notes = ui.textarea("Notes", value=t.notes).classes("w-full").props("autofocus autogrow")

                def save() -> None:
                    ledger.set_fields(t.id, notes=notes.value or "")
                    dlg.close()
                    months.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", on_click=save)
            dlg.open()

        def fill_group_menu() -> None:
            """Existing groups (newest first), then a new one."""
            group_menu.clear()
            with group_menu:
                for gid, name in groups.options().items():
                    ui.menu_item(name, on_click=lambda gid=gid, name=name: add_to(gid, name))
                ui.separator()
                ui.menu_item("New group…", on_click=lambda: new_group_dialog())

        def add_to(group_id: int, name: str) -> None:
            try:
                n = groups.add_to_group(list(state["selected"]), group_id)
            except groups.GroupError as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(f"Added {n} to “{name}”" if n else f"Already in “{name}”", type="positive")
            state["selected"].clear()
            update_selection()
            months.refresh()

        async def new_group_dialog() -> None:
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label(f"New group for {len(state['selected'])} transaction"
                         f"{'s' if len(state['selected']) != 1 else ''}").classes("font-medium")
                name = ui.input("Group name", placeholder="e.g. Vietnam trip, Nov 2026").classes("w-full") \
                    .props("autofocus")
                name.on("keydown.enter", lambda: dlg.submit(name.value))
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Create", on_click=lambda: dlg.submit(name.value))
            if (group_name := await dlg) is None:
                return
            try:
                gid = groups.create_group(group_name)
            except groups.GroupError as exc:
                ui.notify(str(exc), type="warning")
                return
            add_to(gid, " ".join(group_name.split()))

        def ungroup() -> None:
            try:
                groups.remove_from_group(list(state["selected"]))
            except groups.GroupError as exc:  # a group with a split on it
                ui.notify(str(exc), type="warning")
                return
            state["selected"].clear()
            update_selection()
            months.refresh()

        update_selection()
        with ui.column().classes("w-full gap-3"):
            months()
        on_hide_change(months)  # open months and the search stay as they are
