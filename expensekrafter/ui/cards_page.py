from __future__ import annotations

from datetime import date

from nicegui import ui
from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, AccountType, Direction, Transaction
from ..services import cards, ledger
from ..services.money import fmt, to_minor
from . import icons
from .layout import frame, on_hide_change

def _due_chip(x: cards.StatementStatus) -> tuple[str, str]:
    """(chip css class, text) for the due-date status."""
    if x.status == "paid":
        return "", "✓ Marked paid" if x.expected else "✓ Paid"
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
                    with ui.column().classes("gap-1 w-40 flex-none"):
                        if x.pending_minor:
                            ui.button("Mark as paid", icon="task_alt", on_click=lambda x=x: mark_dialog(x)) \
                                .props("unelevated no-caps").classes("w-full") \
                                .tooltip("Paid, but that bank statement isn't imported yet")
                            ui.button("Link payment", icon="link", on_click=lambda x=x: link_dialog(x)) \
                                .props("outline no-caps").classes("w-full") \
                                .tooltip("Pick the debit from an imported bank statement")
                # Payments marked by hand, until their bank statement is imported.
                for pay in x.expected:
                    with ui.row().classes("w-full items-center gap-3 no-wrap pt-2 et-hero-rule"):
                        ui.icon("schedule").classes("et-faint")
                        ui.label(f"{fmt(pay.amount_minor)} from {pay.account} on {pay.paid_on:%d %b %Y}"
                                 + (f" · “{pay.note}”" if pay.note else "")).classes("text-sm flex-1 truncate")
                        ui.label("waiting for bank statement").classes("et-chip flex-none") \
                            .tooltip("Matched to the real debit when you import that bank statement")
                        ui.button("Attach", icon="link", on_click=lambda pay=pay, x=x: attach_dialog(x, pay)) \
                            .props("flat dense no-caps").tooltip("Pick the bank debit by hand")
                        ui.button(icon="undo", on_click=lambda pay=pay: undo(pay)) \
                            .props("flat round dense").tooltip("Undo Mark as paid")

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

        def mark_dialog(x: cards.StatementStatus) -> None:
            banks = {a.id: a.name for a in ledger.accounts() if a.type == AccountType.bank}
            with ui.dialog() as dlg, ui.card().classes("w-[32rem] max-w-full gap-3"):
                ui.label(f"Mark {x.account_name} bill as paid").classes("et-card-title")
                ui.label("For a payment whose bank statement isn't imported yet. The bill shows as paid now, and "
                         "the payment is matched to the real debit when you import that statement.") \
                    .classes("text-sm et-muted")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    usual = cards.usual_payment_account(x.statement.account_id)
                    account = ui.select(banks, label="Paid from",
                                        value=usual if usual in banks else next(iter(banks), None)) \
                        .props("outlined dense options-dense").classes("flex-1")
                    paid_on = ui.input("Paid on", value=date.today().isoformat()) \
                        .props("outlined dense type=date").classes("w-40")
                    amount = ui.number("Amount ₹", value=x.pending_minor / 100, min=0, format="%.2f") \
                        .props("outlined dense stack-label").classes("w-36")
                note = ui.input("Note (optional)", placeholder="e.g. via CRED").props("outlined dense") \
                    .classes("w-full")

                def save() -> None:
                    try:
                        cards.mark_paid(x.statement.id, account.value, date.fromisoformat(paid_on.value),
                                        to_minor(amount.value or 0), note.value or "")
                    except (cards.CardError, TypeError, ValueError) as exc:
                        ui.notify(str(exc) if isinstance(exc, cards.CardError) else "Pick the date",
                                  type="warning")
                        return
                    ui.notify("Marked as paid", type="positive")
                    dlg.close()
                    statement_list.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Mark as paid", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def attach_dialog(x: cards.StatementStatus, pay: cards.ExpectedPayment) -> None:
            nearby = cards.payment_candidates(pay.txn_id)
            options = nearby or cards.payment_candidates(pay.txn_id, nearby_only=False)
            with ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-3"):
                ui.label(f"Attach the {x.account_name} payment").classes("et-card-title")
                ui.label(f"{fmt(pay.amount_minor)} from {pay.account} around {pay.paid_on:%d %b %Y}") \
                    .classes("et-muted")
                if not nearby:
                    ui.label("No debit with that amount near that date; showing all debits on that account."
                             if options else f"No debits on {pay.account} yet. Import its statement first.") \
                        .classes("et-warn text-sm")
                choice = ui.select({t.id: f"{t.txn_date:%d %b %Y} · {fmt(t.amount_minor)} · {t.description[:40]}"
                                    for t in options}, label="Bank debit", with_input=True,
                                   value=nearby[0].id if nearby else None) \
                    .props("outlined dense options-dense").classes("w-full")

                def attach() -> None:
                    if choice.value is None:
                        return
                    try:
                        cards.attach_payment(pay.txn_id, choice.value)
                    except cards.CardError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    ui.notify("Attached", type="positive")
                    dlg.close()
                    statement_list.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Attach", icon="link", on_click=attach)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def undo(pay: cards.ExpectedPayment) -> None:
            cards.unmark_paid(pay.txn_id)
            ui.notify("Mark as paid undone")
            statement_list.refresh()

        statement_list()
        on_hide_change(statement_list)
