"""Transactions as a month → day accordion with inline label/type/notes editing."""

from __future__ import annotations

import re
from datetime import date
from html import escape
from itertools import groupby

from nicegui import ui

from ..db.models import Direction, TxnKind
from ..services import ledger, narration
from ..services.analytics import merchant_name
from ..services.money import fmt
from . import icons
from .layout import frame

KIND_LABELS = {
    TxnKind.expense: "Expense", TxnKind.income: "Income", TxnKind.transfer: "Transfer",
    TxnKind.cc_payment: "Card payment", TxnKind.reimbursement: "Reimbursement", TxnKind.refund: "Refund",
}


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
        state: dict = {"account": None, "unlabelled": False, "selected": set(), "open": set()}

        with ui.row().classes("w-full items-center gap-4"):
            ui.select({None: "All accounts"} | accounts, value=None,
                      on_change=lambda e: (state.update(account=e.value), months.refresh())) \
                .props("outlined dense options-dense").classes("w-56")
            ui.switch("Unlabelled only", on_change=lambda e: (state.update(unlabelled=e.value), months.refresh())) \
                .classes("et-muted")
            ui.space()
            selection_label = ui.label().classes("text-sm et-muted")
            group_btn = ui.button("Group selected", icon="merge", on_click=lambda: group_dialog()).props("outline")
            ungroup_btn = ui.button("Ungroup", icon="call_split", on_click=lambda: ungroup()).props("outline")

        def update_selection() -> None:
            n = len(state["selected"])
            selection_label.text = f"{n} selected" if n else ""
            group_btn.set_enabled(n >= 2)
            ungroup_btn.set_enabled(n >= 1)

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
                    ui.html(f'<span class="et-chip accent">{escape(t.group_name)}</span>', sanitize=False) \
                        .tooltip("Grouped expense")
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
            txns = ledger.month_transactions(month, state["account"], state["unlabelled"])
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
            summaries = ledger.month_summaries(state["account"], state["unlabelled"])
            if not summaries:
                with ui.column().classes("w-full items-center py-16 gap-2"):
                    ui.icon("receipt_long").classes("text-5xl et-faint")
                    ui.label("No transactions for this filter.").classes("et-muted")
                return
            if not state["open"]:
                state["open"].add(summaries[0].month)  # newest month open by default
            for ms in summaries:
                is_open = ms.month in state["open"]
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

        async def group_dialog() -> None:
            ids = list(state["selected"])
            with ui.dialog() as dlg, ui.card():
                ui.label(f"Group {len(ids)} transactions as one expense").classes("font-medium")
                name = ui.input("Group name").classes("w-80").props("autofocus")
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Group", on_click=lambda: dlg.submit(name.value))
            if group_name := await dlg:
                ledger.group_transactions(ids, group_name)
                state["selected"].clear()
                update_selection()
                months.refresh()

        def ungroup() -> None:
            ledger.ungroup_transactions(list(state["selected"]))
            state["selected"].clear()
            update_selection()
            months.refresh()

        update_selection()
        with ui.column().classes("w-full gap-3"):
            months()
