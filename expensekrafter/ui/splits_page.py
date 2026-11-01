"""Splits & reimbursements: expenses (single transactions or groups, like a trip)
that friends or your company pay you back for, who still owes what, and their
repayments. Also reimbursements your employer pays inside your salary."""

from __future__ import annotations

from datetime import date

from nicegui import app, ui

from ..db.models import AccountType
from ..services import ledger, payroll, splits
from ..services.money import fmt, to_minor
from .layout import frame

SHOW = {"open": "Open", "settled": "Settled", "all": "All"}


def _rupees(minor: int) -> str:
    return f"₹{minor / 100:,.0f}" if minor % 100 == 0 else fmt(minor)


def _kpi(title: str, value: str, icon: str, sub: str = "") -> None:
    with ui.card().classes("et-kpi et-card-hover et-rise flex-1 min-w-52"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("et-muted text-sm font-medium")
            with ui.element("div").classes("et-kpi-icon"):
                ui.icon(icon).classes("text-lg")
        ui.label(value).classes("et-kpi-value et-num mt-3")
        if sub:
            ui.label(sub).classes("text-xs mt-1 et-faint")


def _bar(done: float) -> None:
    ui.html('<div style="height:4px;border-radius:2px;background:var(--surface-3);overflow:hidden">'
            f'<div style="width:{min(max(done, 0), 1) * 100:.1f}%;height:100%;border-radius:6px;'
            'background:var(--accent-grad)"></div></div>', sanitize=False).classes("w-full")


def _expense_option(t, acc: str) -> str:
    return f"{t.txn_date:%d %b %Y} · {splits.merchant_name(t.description)} · {acc} · {fmt(t.amount_minor)}"


def _credit_option(c: splits.Credit) -> str:
    left = f" ({fmt(c.free_minor)} left)" if c.linked_minor else ""
    return f"{c.txn_date:%d %b %Y} · {c.name} · {c.account} · {fmt(c.amount_minor)}{left}"


@ui.page("/splits")
def splits_page(new: str = "") -> None:
    """`new`: open New split for this expense, e.g. "g:3" (a group)."""
    with frame("Splits & reimbursements", "Money friends or your company owe you back, what's come in, and "
                                          "reimbursements in your salary"):
        state = {"show": "open"}

        with ui.row().classes("w-full items-center gap-4"):
            ui.toggle(SHOW, value="open", on_change=lambda e: (state.update(show=e.value), content.refresh())) \
                .props("unelevated no-caps toggle-color=primary color=transparent text-color=grey-8 "
                       "padding='6px 14px'").classes("et-segment")
            ui.space()
            ui.button("New split / reimbursement", icon="add", on_click=lambda: split_dialog(None))

        @ui.refreshable
        def content() -> None:
            everything = splits.splits()
            open_ = [sp for sp in everything if not sp.settled or sp.expected]
            pending = [sp for sp in everything if sp.expected]
            with ui.row().classes("w-full gap-4"):
                owed = sum(sp.remaining_minor for sp in everything)
                waiting = sorted({p.person for sp in open_ for p in sp.people if p.remaining_minor})
                _kpi("Owed to you", _rupees(owed), "call_received",
                     f"by {len(waiting)} {'person' if len(waiting) == 1 else 'people'}" if waiting else "all settled")
                _kpi("Paid back", _rupees(sum(sp.paid_minor for sp in everything)), "savings",
                     f"of {_rupees(sum(sp.owed_minor for sp in everything))} shared")
                _kpi("Open splits", str(len(open_)), "call_split",
                     f"{len(pending)} waiting for a card statement" if pending
                     else f"{len(everything) - len(open_)} settled")

            loose = splits.unassigned_reimbursements()
            if loose:
                with ui.card().classes("w-full gap-2"):
                    ui.label("Repayments not linked to a split").classes("font-medium")
                    ui.label("Marked as reimbursements but not tied to an expense yet. Link them so the "
                             "spend is taken off the right expense.").classes("text-xs et-muted")
                    for c in loose:
                        with ui.row().classes("w-full items-center gap-3 no-wrap"):
                            ui.label(f"{c.txn_date:%d %b %Y}").classes("et-faint text-sm w-24 flex-none")
                            ui.label(c.name).classes("flex-1 truncate")
                            ui.label(c.account).classes("et-faint text-sm")
                            ui.label(fmt(c.free_minor)).classes("et-num font-medium w-28 text-right")
                            ui.button("Link", icon="link", on_click=lambda c=c: link_dialog(c)) \
                                .props("outline dense no-caps")

            shown = {"open": open_, "settled": [sp for sp in everything if sp.settled],
                     "all": everything}[state["show"]]
            if not shown:
                with ui.column().classes("w-full items-center py-16 gap-2"):
                    ui.icon("call_split").classes("text-5xl et-faint")
                    ui.label("Paid for friends, or for a work trip? Click New split / reimbursement, pick the "
                             "expense or group and say who pays you back."
                             if not everything else "Nothing here.").classes("et-muted")
                return
            # Collapsible; the header keeps the money still to come in view when it's folded.
            store = app.storage.user
            key = f"splits_list_open_{state['show']}"
            exp = ui.expansion(value=store.get(key, True),
                               on_value_change=lambda e: store.update({key: e.value})) \
                .classes("w-full et-month et-rise")
            to_come = sum(sp.remaining_minor for sp in shown)
            owing = sorted({p.person for sp in shown for p in sp.people if p.remaining_minor}, key=str.casefold)
            waiting = sum(sp.expected for sp in shown)
            with exp.add_slot("header"):
                with ui.row().classes("w-full items-center gap-4 no-wrap"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"{SHOW[state['show']]} splits").classes("et-display text-lg font-semibold")
                        ui.label(f"{len(shown)} split{'s' if len(shown) != 1 else ''}"
                                 + (f" · {waiting} waiting for a card statement" if waiting else "")) \
                            .classes("text-xs et-faint")
                    ui.space()
                    if to_come:
                        with ui.column().classes("gap-0 items-end"):
                            ui.label(f"{_rupees(to_come)} to come").classes("et-num font-semibold et-warn")
                            ui.label(f"from {', '.join(owing[:3])}" + (f" +{len(owing) - 3}" if len(owing) > 3
                                                                        else "")) \
                                .classes("text-xs et-faint truncate max-w-64")
                    else:
                        ui.label("✓ Nothing to come").classes("et-chip accent")
            with exp, ui.column().classes("w-full gap-3 p-4 pt-0"):
                for sp in shown:
                    split_card(sp)

        def split_card(sp: splits.Split) -> None:
            with ui.card().classes("w-full et-card-hover et-rise gap-3"):
                with ui.row().classes("w-full items-start no-wrap gap-4"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(sp.name).classes("font-semibold truncate w-full")
                        details = ([f"{sp.members} transaction{'s' if sp.members != 1 else ''} from "
                                    f"{sp.txn_date:%d %b %Y}"] if sp.group_id
                                   else [f"{sp.txn_date:%d %b %Y}"]) + [sp.account] + ([sp.label] if sp.label else [])
                        ui.label(" · ".join(details)).classes("text-xs et-faint")
                    with ui.column().classes("gap-0 items-end"):
                        ui.label(fmt(sp.amount_minor)).classes("et-num font-semibold")
                        ui.label(f"your share {fmt(sp.your_share_minor)}").classes("text-xs et-faint et-num")
                    if sp.expected:
                        age = (date.today() - sp.txn_date).days
                        overdue = age > splits.PENDING_OVERDUE_DAYS
                        ui.label("Not in a statement yet" if overdue else "Waiting for statement") \
                            .classes("et-chip warn flex-none" if overdue else "et-chip flex-none") \
                            .tooltip(f"{age} days and no imported transaction matched it: check the amount and "
                                     "account, or attach it by hand" if overdue else
                                     "Attached automatically when you import the statement with it")
                    if sp.settled:
                        ui.label("✓ Settled").classes("et-chip accent flex-none")
                    else:
                        ui.label(f"{fmt(sp.remaining_minor)} to come").classes("et-chip warn flex-none")
                    with ui.button(icon="more_vert").props("flat round dense"):
                        with ui.menu():
                            if sp.expected:
                                ui.menu_item("Attach to imported transaction", on_click=lambda sp=sp: attach_dialog(sp))
                                ui.menu_item("Edit date, amount, name", on_click=lambda sp=sp: pending_dialog(sp))
                            ui.menu_item("Edit shares", on_click=lambda sp=sp: split_dialog(sp))
                            ui.menu_item("Delete split", on_click=lambda sp=sp: confirm_delete(sp))
                for p in sp.people:
                    with ui.row().classes("w-full items-center gap-4 no-wrap"):
                        ui.label(p.person).classes("w-40 truncate font-medium")
                        with ui.column().classes("flex-1 gap-1"):
                            _bar(p.paid_minor / p.owed_minor if p.owed_minor else 1)
                            ui.label(f"paid {fmt(p.paid_minor)} of {fmt(p.owed_minor)}") \
                                .classes("text-xs et-faint et-num")
                        if p.remaining_minor:
                            ui.button(f"Record {fmt(p.remaining_minor)}", icon="call_received",
                                      on_click=lambda sp=sp, p=p: repayment_dialog(sp, p)) \
                                .props("outline dense no-caps").classes("w-44")
                        else:
                            ui.label("✓ Paid").classes("et-muted text-sm w-44 text-right")
                if sp.repayments:
                    with ui.column().classes("w-full gap-0 pt-2 et-hero-rule"):
                        for r in sp.repayments:
                            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                                ui.icon("subdirectory_arrow_right").classes("et-faint")
                                ui.label(f"{fmt(r.amount_minor)} from {r.person} · {r.credit_date:%d %b %Y} · "
                                         f"{r.credit_account}").classes("text-sm et-muted flex-1")
                                ui.button(icon="link_off", on_click=lambda r=r: unlink(r)) \
                                    .props("flat round dense size=sm").tooltip("Remove this repayment")

        def act(fn, *args, done: str = "", **kwargs) -> bool:
            try:
                fn(*args, **kwargs)
            except splits.SplitError as e:
                ui.notify(str(e), type="warning")
                return False
            if done:
                ui.notify(done, type="positive")
            content.refresh()
            return True

        def unlink(r: splits.Repayment) -> None:
            act(splits.remove_repayment, r.link_id, done="Repayment removed")

        def confirm_delete(sp: splits.Split) -> None:
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label(f"Delete the split for {sp.name}?").classes("font-medium")
                ui.label("Shares and repayment links go. The expense and the credits stay; credits already "
                         "marked as reimbursements still count against spend on the day they came in.") \
                    .classes("text-sm et-muted")
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Delete", icon="delete",
                              on_click=lambda: act(splits.delete_split, sp.txn_id, sp.group_id, done="Split deleted")
                              and dlg.close()).props("color=negative")
            dlg.open()

        def split_dialog(sp: splits.Split | None, preselect: str | None = None) -> None:
            known = splits.people()
            rows: list[dict] = []
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-3"):
                ui.label("Edit split" if sp else "New split / reimbursement").classes("et-card-title")
                if sp:
                    ui.label(f"{sp.name} · {sp.txn_date:%d %b %Y} · {fmt(sp.amount_minor)}").classes("et-muted")
                    total = {"minor": sp.amount_minor}
                    expense = None
                else:
                    candidates = splits.expense_candidates(limit=400)
                    groups = splits.group_candidates()
                    # Groups first (e.g. a whole trip), then single transactions.
                    options = {f"g:{g.group_id}": f"Group · {g.name} · {g.count} transaction"
                                                  f"{'s' if g.count != 1 else ''} from "
                                                  f"{g.first_date:%d %b %Y} · {fmt(g.amount_minor)}" for g in groups}
                    options |= {f"t:{t.id}": _expense_option(t, acc) for t, acc in candidates}
                    amounts = {f"g:{g.group_id}": g.amount_minor for g in groups}
                    amounts |= {f"t:{t.id}": t.amount_minor for t, _ in candidates}
                    total = {"minor": 0}
                    mode = ui.toggle({"imported": "Already imported", "pending": "Card statement not in yet"},
                                     value="imported", on_change=lambda e: switch_mode()) \
                        .props("unelevated no-caps dense toggle-color=primary color=transparent "
                               "text-color=grey-8 padding='4px 12px'").classes("et-segment")
                    with ui.column().classes("w-full gap-2") as imported_box:
                        expense = ui.select(options, label="Expense or group you paid", with_input=True,
                                            on_change=lambda e: (total.update(minor=amounts.get(e.value, 0)),
                                                                 refresh_share())) \
                            .props("outlined dense options-dense").classes("w-full")
                        ui.label("Type to search by name, account or amount. Several transactions for one "
                                 "thing (a trip)? Put them in an expense group first.") \
                            .classes("text-xs et-faint -mt-1")
                    with ui.column().classes("w-full gap-2") as pending_box:
                        ui.label("Record it now; it's matched to the card transaction when you import that "
                                 "statement.").classes("text-xs et-muted")
                        accounts = ledger.accounts()
                        cards = [a for a in accounts if a.type == AccountType.credit_card]
                        with ui.row().classes("w-full gap-2 no-wrap"):
                            p_account = ui.select({a.id: a.name for a in accounts}, label="Paid with",
                                                  value=(cards or accounts)[0].id if accounts else None) \
                                .props("outlined dense options-dense").classes("flex-1")
                            p_date = ui.input("Date", value=date.today().isoformat()) \
                                .props("outlined dense type=date").classes("w-40")
                            p_amount = ui.number("Amount ₹", min=0, format="%.2f",
                                                 on_change=lambda e: (total.update(minor=to_minor(e.value or 0)),
                                                                      refresh_share())) \
                                .props("outlined dense stack-label").classes("w-36")
                        with ui.row().classes("w-full gap-2 no-wrap"):
                            p_name = ui.input("What for", placeholder="e.g. Dinner at Toit") \
                                .props("outlined dense").classes("flex-1")
                            p_label = ui.select({lab.id: lab.name for lab in ledger.labels()}, label="Label",
                                                with_input=True, clearable=True) \
                                .props("outlined dense options-dense").classes("w-48")
                    pending_box.set_visibility(False)

                    def switch_mode() -> None:
                        is_pending = mode.value == "pending"
                        imported_box.set_visibility(not is_pending)
                        pending_box.set_visibility(is_pending)
                        total.update(minor=to_minor(p_amount.value or 0) if is_pending
                                     else amounts.get(expense.value, 0))
                        refresh_share()

                ui.label("Who pays you back").classes("text-sm font-medium mt-2")
                people_box = ui.column().classes("w-full gap-2")
                share_line = ui.label().classes("text-sm et-num")

                def refresh_share() -> None:
                    others = sum(to_minor(r["amount"].value or 0) for r in rows)
                    mine = total["minor"] - others
                    pending_mode = not sp and mode.value == "pending"
                    share_line.text = (f"Your share: {fmt(mine)}" if total["minor"]
                                       else "Enter what you paid" if pending_mode else "Pick the expense first")
                    share_line.classes(replace="text-sm et-num " + ("et-warn" if mine < 0 else ""))

                def add_row(name: str = "", amount: float | None = None) -> None:
                    with people_box, ui.row().classes("w-full items-center gap-2 no-wrap") as line:
                        name_in = ui.input("Name", value=name, autocomplete=known) \
                            .props("outlined dense").classes("flex-1")
                        amount_in = ui.number("Owes ₹", value=amount, min=0, format="%.2f",
                                              on_change=lambda: refresh_share()) \
                            .props("outlined dense stack-label").classes("w-40")
                        row = {"name": name_in, "amount": amount_in, "line": line}
                        ui.button(icon="close", on_click=lambda: remove_row(row)).props("flat round dense")
                    rows.append(row)
                    refresh_share()

                def remove_row(row: dict) -> None:
                    rows.remove(row)
                    people_box.remove(row["line"])
                    refresh_share()

                def split_equally() -> None:
                    if not total["minor"] or not rows:
                        return
                    each = total["minor"] // (len(rows) + 1)  # you are one of the people; rounding stays with you
                    for r in rows:
                        r["amount"].value = each / 100
                    refresh_share()

                with ui.row().classes("gap-2"):
                    ui.button("Add person", icon="person_add", on_click=lambda: add_row()).props("flat no-caps")
                    ui.button("Split equally with me", icon="balance", on_click=split_equally) \
                        .props("flat no-caps")

                for p in (sp.people if sp else []):
                    add_row(p.person, p.owed_minor / 100)
                if not rows:
                    add_row()
                if not sp and preselect in options:  # opened from Expense groups
                    expense.set_value(preselect)
                elif not sp and preselect:
                    ui.notify("That group can't be split: it has nothing paid out once its money in is taken off, "
                              "or it already has a split.", type="warning", multi_line=True)

                def save() -> None:
                    shares = {r["name"].value or "": to_minor(r["amount"].value or 0) for r in rows}
                    if len(shares) < len(rows):
                        ui.notify("Each person once, please", type="warning")
                        return
                    if not sp and mode.value == "pending":
                        try:
                            day = date.fromisoformat(p_date.value)
                        except (TypeError, ValueError):
                            ui.notify("Pick the date", type="warning")
                            return
                        if act(splits.create_pending, p_account.value, day, to_minor(p_amount.value or 0),
                               p_name.value or "", shares, p_label.value, done="Split saved; it'll be matched "
                                                                                "when the statement comes in"):
                            dlg.close()
                        return
                    if sp:
                        txn_id, group_id = sp.txn_id, sp.group_id
                    elif expense.value is None:
                        ui.notify("Pick the expense first", type="warning")
                        return
                    else:
                        kind, ident = expense.value.split(":")
                        txn_id, group_id = (None, int(ident)) if kind == "g" else (int(ident), None)
                    if act(splits.save_split, txn_id, shares, group_id, done="Split saved"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def pending_dialog(sp: splits.Split) -> None:
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[30rem] max-w-full gap-3"):
                ui.label("Edit pending split").classes("et-card-title")
                ui.label(f"Paid with {sp.account}").classes("et-muted text-sm")
                name = ui.input("What for", value=sp.name).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    day = ui.input("Date", value=sp.txn_date.isoformat()).props("outlined dense type=date") \
                        .classes("flex-1")
                    amount = ui.number("Amount ₹", value=sp.amount_minor / 100, min=0, format="%.2f") \
                        .props("outlined dense stack-label").classes("flex-1")

                def save() -> None:
                    try:
                        when = date.fromisoformat(day.value)
                    except (TypeError, ValueError):
                        ui.notify("Pick the date", type="warning")
                        return
                    if act(splits.update_pending, sp.txn_id, txn_date=when, amount_minor=to_minor(amount.value or 0),
                           description=name.value or "", done="Saved"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def attach_dialog(sp: splits.Split) -> None:
            nearby = splits.attach_candidates(sp.txn_id)
            options = nearby or splits.attach_candidates(sp.txn_id, nearby_only=False)
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-3"):
                ui.label(f"Attach “{sp.name}”").classes("et-card-title")
                ui.label(f"{fmt(sp.amount_minor)} on {sp.account} around {sp.txn_date:%d %b %Y}").classes("et-muted")
                if not nearby:
                    ui.label("Nothing on that account with the same amount near that date; showing all its "
                             "expenses." if options else f"No imported expenses on {sp.account} yet. Import its "
                             "statement first.").classes("et-warn text-sm")
                choice = ui.select({t.id: _expense_option(t, acc) for t, acc in options}, with_input=True,
                                   label="Imported transaction", value=options[0][0].id if nearby else None) \
                    .props("outlined dense options-dense").classes("w-full")
                ui.label("Its shares and repayments move onto that transaction.").classes("text-xs et-faint")

                def attach() -> None:
                    if choice.value is None:
                        return
                    if act(splits.attach_pending, sp.txn_id, choice.value, done="Attached"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Attach", icon="link", on_click=attach)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def repayment_dialog(sp: splits.Split, p: splits.PersonShare) -> None:
            credits = {c.txn_id: c for c in splits.credit_candidates(limit=400)}
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-3"):
                ui.label(f"Money back from {p.person}").classes("et-card-title")
                ui.label(f"For {sp.name} · {sp.txn_date:%d %b %Y} · still owes {fmt(p.remaining_minor)}") \
                    .classes("et-muted")
                if not credits:
                    ui.label("No incoming money left to assign. Import the statement with their payment first.") \
                        .classes("et-warn text-sm")
                credit = ui.select({k: _credit_option(c) for k, c in credits.items()}, label="Money that came in",
                                   with_input=True,
                                   on_change=lambda e: amount.set_value(
                                       min(p.remaining_minor, credits[e.value].free_minor) / 100 if e.value else None)) \
                    .props("outlined dense options-dense").classes("w-full")
                ui.label("Type their name or the amount to find it.").classes("text-xs et-faint -mt-2")
                amount = ui.number("Amount for this split ₹", min=0, format="%.2f").props("outlined dense stack-label") \
                    .classes("w-56")

                def save() -> None:
                    if credit.value is None:
                        ui.notify("Pick the credit first", type="warning")
                        return
                    if act(splits.record_repayment, sp.txn_id, credit.value, p.person,
                           to_minor(amount.value or 0), sp.group_id, done="Repayment recorded"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Record", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        def link_dialog(c: splits.Credit) -> None:
            owing = {f"{sp.key}|{p.person}": (sp, p) for sp in splits.splits() for p in sp.people if p.remaining_minor}
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[36rem] max-w-full gap-3"):
                ui.label("Link a repayment").classes("et-card-title")
                ui.label(f"{c.name} · {c.txn_date:%d %b %Y} · {fmt(c.free_minor)} to assign").classes("et-muted")
                if not owing:
                    ui.label("Nobody owes you anything right now. Add a split first.").classes("et-warn text-sm")
                options = {key: f"{p.person} · {sp.name} · {sp.txn_date:%d %b %Y} · owes {fmt(p.remaining_minor)}"
                           for key, (sp, p) in owing.items()}
                target = ui.select(options, label="Who paid, and for what", with_input=True,
                                   on_change=lambda e: amount.set_value(
                                       min(c.free_minor, owing[e.value][1].remaining_minor) / 100
                                       if e.value else None)) \
                    .props("outlined dense options-dense").classes("w-full")
                amount = ui.number("Amount ₹", min=0, format="%.2f").props("outlined dense stack-label") \
                    .classes("w-56")

                def save() -> None:
                    if target.value is None:
                        return
                    sp, p = owing[target.value]
                    if act(splits.record_repayment, sp.txn_id, c.txn_id, p.person, to_minor(amount.value or 0),
                           sp.group_id, done="Repayment linked"):
                        dlg.close()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Link", icon="link", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        content()

        @ui.refreshable
        def salary_section() -> None:
            items = payroll.entries()
            with ui.card().classes("w-full gap-3"):
                with ui.row().classes("w-full items-start no-wrap"):
                    with ui.column().classes("gap-0"):
                        ui.label("Reimbursed in your salary").classes("et-card-title")
                        ui.label("Allowances your employer pays back with the salary (meals, internet...). "
                                 "Each is taken off spend for its label and off income, so only what you "
                                 "paid yourself counts.").classes("text-xs et-muted")
                    ui.space()
                    ui.button("Add", icon="add", on_click=lambda: salary_dialog(None, None))                         .props("outline dense no-caps").classes("flex-none")
                if not items:
                    ui.label("Nothing yet. Add one for each month, using the amount on your payslip.")                         .classes("et-faint text-sm")
                    return
                with ui.column().classes("w-full gap-0"):
                    for e in items:
                        with ui.row().classes("w-full items-center gap-3 no-wrap py-1"):
                            ui.label(f"{e.paid_on:%d %b %Y}").classes("et-faint text-sm w-24 flex-none")
                            ui.html(f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
                                    f'background:{e.color or "#a9aeab"}"></span>', sanitize=False)
                            ui.label(e.label or "No label").classes("w-40 truncate font-medium")
                            ui.label(" · ".join(x for x in (e.note, e.account) if x))                                 .classes("et-muted text-sm flex-1 truncate")
                            ui.label(fmt(e.amount_minor)).classes("et-num font-medium w-28 text-right")
                            with ui.button(icon="more_vert").props("flat round dense"):
                                with ui.menu():
                                    ui.menu_item("Edit", on_click=lambda e=e: salary_dialog(e, None))
                                    ui.menu_item("Add again for the next month",
                                                 on_click=lambda e=e: salary_dialog(None, e))
                                    ui.menu_item("Delete", on_click=lambda e=e: (
                                        payroll.delete(e.id), ui.notify("Removed"), salary_section.refresh()))

        def salary_dialog(entry: payroll.Entry | None, like: payroll.Entry | None) -> None:
            """Edit `entry`, or add a new one (copying `like`, a month later)."""
            src = entry or like
            labels = {lab.id: lab.name for lab in ledger.labels()}
            accounts = {a.id: a.name for a in ledger.accounts()}
            if entry:
                when = entry.paid_on
            elif like:
                y, m = divmod(like.paid_on.year * 12 + like.paid_on.month, 12)
                when = date(y, m + 1, min(like.paid_on.day, 28))
            else:
                when = date.today()
            default_label = next((i for i, n in labels.items() if n.lower().startswith("food")), None)
            with dialog_host, ui.dialog() as dlg, ui.card().classes("w-[32rem] max-w-full gap-3"):
                ui.label("Edit reimbursement" if entry else "Reimbursed in salary").classes("et-card-title")
                ui.label("The part of your salary that pays back an expense, e.g. the meal allowance on "
                         "your payslip. Date it in the month it covers (October's allowance in October, even "
                         "if the salary comes on 1 November).").classes("text-sm et-muted")
                with ui.row().classes("w-full gap-3 no-wrap"):
                    paid_on = ui.input("Counts in", value=when.isoformat()).props("outlined dense type=date")                         .classes("flex-1").tooltip("A day in the month whose spending it pays back")
                    amount = ui.number("Amount ₹", value=src.amount_minor / 100 if src else None, min=0,
                                       format="%.2f").props("outlined dense stack-label").classes("flex-1")
                label = ui.select(labels, label="Pays back (label)",
                                  value=src.label_id if src else default_label, with_input=True)                     .props("outlined dense options-dense").classes("w-full")
                account = ui.select({None: "—"} | accounts, label="Salary account",
                                    value=src.account_id if src else payroll.salary_account())                     .props("outlined dense options-dense").classes("w-full")
                note = ui.input("Note", value=src.note if src else "", placeholder="e.g. HungerBox meal allowance")                     .props("outlined dense").classes("w-full")

                def save() -> None:
                    try:
                        day = date.fromisoformat(paid_on.value)
                    except (TypeError, ValueError):
                        ui.notify("Pick the date", type="warning")
                        return
                    try:
                        payroll.save(entry.id if entry else None, day, to_minor(amount.value or 0),
                                     label.value, account.value, note.value or "")
                    except payroll.PayrollError as e:
                        ui.notify(str(e), type="warning")
                        return
                    ui.notify("Saved", type="positive")
                    dlg.close()
                    salary_section.refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button("Save", icon="check", on_click=save)
            dlg.on("hide", dlg.delete)
            dlg.open()

        salary_section()
        # Dialogs are created here, not inside the card menu that opens them (a
        # closing menu would take its dialog with it).
        dialog_host = ui.element("div")
        if new:
            split_dialog(None, new)

