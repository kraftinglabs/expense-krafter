from __future__ import annotations

from datetime import date

from nicegui import ui
from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, AccountType, Direction, Transaction
from ..services import cards
from ..services.money import fmt, to_minor
from . import icons
from .layout import frame, on_hide_change

def _due_chip(x: cards.StatementStatus) -> tuple[str, str]:
    """(chip css class, text) for the due-date status."""
    if x.status == "paid":
        return "", "✓ Paid"
    days = x.days_to_due
    if days < 0:
        return "neg", f"Overdue by {-days} day{'s' if days != -1 else ''}"
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    return ("warn" if days <= 5 else "accent"), f"Due {when}"


@ui.page("/cards")
def cards_page() -> None:
    with frame("Credit cards", "Statements, payments and what is still due"):
        with get_session() as s:
            card_objs = {a.id: a for a in s.exec(select(Account).where(Account.type == AccountType.credit_card))}
            card_accounts = {i: a.name for i, a in card_objs.items()}
            bank_debits = s.exec(
                select(Transaction, Account.name).join(Account, Account.id == Transaction.account_id)
                .where(Account.type == AccountType.bank, Transaction.direction == Direction.debit,
                       Transaction.expected == False)  # noqa: E712
                .order_by(col(Transaction.txn_date).desc()).limit(300)
            ).all()

        with ui.row().classes("w-full justify-end"):
            ui.button("Add statement", icon="add", on_click=lambda: add_dialog())

        @ui.refreshable
        def statement_list() -> None:
            rows = cards.statement_statuses()
            if not rows:
                with ui.column().classes("w-full items-center py-16 gap-2"):
                    ui.icon("credit_card").classes("text-5xl et-faint")
                    ui.label("No card statements yet. Import a card PDF or add one manually.").classes("et-muted")
                return
            with ui.column().classes("w-full gap-3"):
                for x in rows:
                    statement_card(x)

        def statement_card(x: cards.StatementStatus) -> None:
            total = x.statement.total_due_minor
            paid_pct = min(x.paid_minor / total * 100, 100) if total else 100
            chip_class, chip_text = _due_chip(x)
            with ui.card().classes("w-full et-card-hover et-rise"):
                with ui.row().classes("w-full items-center gap-5 no-wrap"):
                    if acc := card_objs.get(x.statement.account_id):
                        ui.html(icons.tile_html(acc, 48), sanitize=False).classes("flex-none")
                    with ui.column().classes("gap-0 w-56"):
                        ui.label(x.account_name).classes("font-semibold")
                        ui.label(f"Statement {x.statement.statement_date:%d %b %Y}").classes("text-xs et-faint")
                    with ui.column().classes("gap-2 flex-1 min-w-64"):
                        with ui.row().classes("w-full items-baseline justify-between"):
                            ui.label(fmt(x.pending_minor) if x.pending_minor else "Fully paid") \
                                .classes("et-display text-xl font-semibold et-num")
                            ui.label(f"{fmt(x.paid_minor)} of {fmt(total)} paid").classes("text-xs et-faint et-num")
                        ui.html(
                            '<div style="height:4px;border-radius:2px;background:var(--surface-3);overflow:hidden">'
                            f'<div style="width:{paid_pct:.1f}%;height:100%;border-radius:6px;'
                            'background:var(--accent-grad)"></div></div>',
                            sanitize=False).classes("w-full")
                    with ui.column().classes("gap-1 items-end w-48"):
                        ui.html(f'<span class="et-chip {chip_class}">{chip_text}</span>', sanitize=False)
                        ui.label(f"Due {x.statement.due_date:%d %b %Y}").classes("text-xs et-faint")
                    if x.pending_minor:
                        ui.button("Link payment", icon="link", on_click=lambda x=x: link_dialog(x)) \
                            .props("outline")

        def add_dialog() -> None:
            if not card_accounts:
                ui.notify("Add a credit card account first", type="warning")
                return
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                acc = ui.select(card_accounts, value=next(iter(card_accounts)), label="Card").classes("w-full")
                st_date = ui.input("Statement date", value=date.today().isoformat()).props("type=date")
                due = ui.input("Due date").props("type=date")
                total = ui.number("Total due", format="%.2f")
                min_due = ui.number("Minimum due", format="%.2f", value=0)

                def save() -> None:
                    cards.add_statement(acc.value, date.fromisoformat(st_date.value), date.fromisoformat(due.value),
                                        to_minor(total.value or 0), to_minor(min_due.value or 0))
                    dlg.close()
                    statement_list.refresh()

                ui.button("Save", on_click=save)
            dlg.open()

        def link_dialog(x: cards.StatementStatus) -> None:
            options = {t.id: f"{t.txn_date:%d %b} · {acc} · {fmt(t.amount_minor)} · {t.description[:40]}"
                       for t, acc in bank_debits}
            with ui.dialog() as dlg, ui.card().classes("w-[36rem]"):
                ui.label(f"Pay {x.account_name} statement · pending {fmt(x.pending_minor)}")
                txn = ui.select(options, label="Bank debit", with_input=True).classes("w-full")
                amount = ui.number("Amount applied", value=x.pending_minor / 100, format="%.2f")

                def save() -> None:
                    if txn.value is None:
                        return
                    cards.link_payment(x.statement.id, txn.value, to_minor(amount.value or 0))
                    dlg.close()
                    statement_list.refresh()

                ui.label("Link several debits one by one if the bill was paid in parts.").classes("text-xs et-muted")
                ui.button("Link", on_click=save)
            dlg.open()

        statement_list()
        on_hide_change(statement_list)
