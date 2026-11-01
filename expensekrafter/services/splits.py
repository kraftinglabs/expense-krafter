"""Shared expenses: who owes you what for an expense you paid, and the money
they've sent back.

A split is an expense with one SplitShare per person. Repayments are bank
credits linked to the expense with a ReimbursementLink (one credit can cover
several splits or people, one person can pay in parts). Spend analysis takes
linked repayments off the expense itself, so only your share counts as spend
once everyone has paid.

Pending splits: a card expense whose statement hasn't been imported yet is
recorded as a placeholder transaction (expected=True) on that account. It
holds the shares and any repayments, never counts as spend (so a repayment
linked to it is held aside rather than counted), and is swapped for the real
row when the statement comes in (attach_pending).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import col, select

from ..db import get_session
from ..db.models import Account, Direction, Label, ReimbursementLink, SplitShare, Transaction, TxnKind
from .analytics import merchant_name
from .money import fmt


class SplitError(ValueError):
    """A split or repayment that doesn't add up; the message says why."""


@dataclass
class Repayment:
    link_id: int
    credit_txn_id: int
    credit_date: date
    credit_account: str
    person: str
    amount_minor: int


@dataclass
class PersonShare:
    person: str
    owed_minor: int
    paid_minor: int = 0

    @property
    def remaining_minor(self) -> int:
        return max(self.owed_minor - self.paid_minor, 0)


@dataclass
class Split:
    txn_id: int
    txn_date: date
    description: str
    account: str
    label: str
    amount_minor: int
    people: list[PersonShare] = field(default_factory=list)
    repayments: list[Repayment] = field(default_factory=list)
    expected: bool = False  # pending: the card statement with it isn't imported yet
    account_id: int | None = None

    @property
    def name(self) -> str:
        return self.description if self.expected else merchant_name(self.description)

    @property
    def owed_minor(self) -> int:
        return sum(p.owed_minor for p in self.people)

    @property
    def paid_minor(self) -> int:
        return sum(r.amount_minor for r in self.repayments)

    @property
    def remaining_minor(self) -> int:
        return sum(p.remaining_minor for p in self.people)

    @property
    def your_share_minor(self) -> int:
        return self.amount_minor - self.owed_minor

    @property
    def settled(self) -> bool:
        return self.remaining_minor == 0


@dataclass
class Credit:
    """A bank credit that could be (part of) a repayment."""

    txn_id: int
    txn_date: date
    account: str
    description: str
    kind: TxnKind
    amount_minor: int
    linked_minor: int

    @property
    def free_minor(self) -> int:
        return self.amount_minor - self.linked_minor

    @property
    def name(self) -> str:
        return merchant_name(self.description)


def _clean_shares(amount_minor: int, shares: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for person, amount in shares.items():
        person = " ".join(person.split())
        if not person:
            raise SplitError("Every share needs a name.")
        if person.casefold() in {p.casefold() for p in out}:
            raise SplitError(f"{person} is listed twice.")
        if amount <= 0:
            raise SplitError(f"{person}'s share must be more than zero.")
        out[person] = amount
    if not out:
        raise SplitError("Add at least one person who owes you.")
    if sum(out.values()) > amount_minor:
        raise SplitError(f"The shares add up to {fmt(sum(out.values()))}, more than the expense "
                         f"({fmt(amount_minor)}).")
    return out


def save_split(expense_txn_id: int, shares: dict[str, int]) -> None:
    """Set who owes what for an expense, replacing any earlier shares.
    Repayments already recorded stay."""
    with get_session() as s:
        txn = s.get(Transaction, expense_txn_id)
        if txn is None or txn.direction != Direction.debit:
            raise SplitError("Only money you paid out can be split.")
        clean = _clean_shares(txn.amount_minor, shares)
        paid = _paid_by_person(s, [expense_txn_id]).get(expense_txn_id, {})
        for person, amount in paid.items():
            owed = next((v for k, v in clean.items() if k.casefold() == person.casefold()), 0)
            if amount > owed:
                raise SplitError(f"{person} has already paid back {fmt(amount)}; their share can't be less, "
                                 "or remove that repayment first.")
        for old in s.exec(select(SplitShare).where(SplitShare.expense_txn_id == expense_txn_id)):
            s.delete(old)
        s.add_all(SplitShare(expense_txn_id=expense_txn_id, person=p, amount_minor=a) for p, a in clean.items())
        s.commit()


def delete_split(expense_txn_id: int) -> None:
    """Forget the split and its repayment links. The repayment credits stay
    marked as reimbursements, so they still count against spend on the day
    they arrived. A pending split's placeholder goes too."""
    with get_session() as s:
        for row in s.exec(select(SplitShare).where(SplitShare.expense_txn_id == expense_txn_id)):
            s.delete(row)
        for row in s.exec(select(ReimbursementLink).where(ReimbursementLink.expense_txn_id == expense_txn_id)):
            s.delete(row)
        s.flush()
        if (txn := s.get(Transaction, expense_txn_id)) is not None and txn.expected:
            s.delete(txn)
        s.commit()


# How far the statement date may be from the date you entered for a pending split.
MATCH_DAYS = 7
# A pending split this old is probably attached to the wrong account or amount.
PENDING_OVERDUE_DAYS = 45


def create_pending(account_id: int, txn_date: date, amount_minor: int, description: str,
                   shares: dict[str, int], label_id: int | None = None) -> int:
    """Record a split for a card expense that isn't imported yet. Returns the
    placeholder's id (use it like any split's txn_id)."""
    description = " ".join((description or "").split())
    if not description:
        raise SplitError("Say what it was for, so you can recognise it later.")
    if amount_minor <= 0:
        raise SplitError("Enter what you paid.")
    _clean_shares(amount_minor, shares)
    with get_session() as s:
        if s.get(Account, account_id) is None:
            raise SplitError("Pick the card or account you paid with.")
        txn = Transaction(account_id=account_id, txn_date=txn_date, description=description,
                          amount_minor=amount_minor, direction=Direction.debit, kind=TxnKind.expense,
                          kind_manual=True, label_id=label_id, fingerprint=f"expected:{uuid.uuid4().hex}",
                          expected=True)
        s.add(txn)
        s.commit()
        txn_id = txn.id
    save_split(txn_id, shares)
    return txn_id


def update_pending(txn_id: int, *, txn_date: date, amount_minor: int, description: str) -> None:
    with get_session() as s:
        txn = s.get(Transaction, txn_id)
        if txn is None or not txn.expected:
            raise SplitError("That split is already attached to its transaction.")
        owed = sum(sh.amount_minor for sh in s.exec(select(SplitShare).where(SplitShare.expense_txn_id == txn_id)))
        if amount_minor < owed:
            raise SplitError(f"The shares add up to {fmt(owed)}, more than {fmt(amount_minor)}.")
        txn.txn_date, txn.amount_minor = txn_date, amount_minor
        txn.description = " ".join(description.split()) or txn.description
        s.add(txn)
        s.commit()


def attach_candidates(pending_id: int, txn_ids: set[int] | None = None, nearby_only: bool = True
                      ) -> list[tuple[Transaction, str]]:
    """Imported expenses on the pending split's account it could be, best
    match first: same amount within MATCH_DAYS of its date (or, with
    nearby_only=False, any expense on that account not split yet)."""
    with get_session() as s:
        pending = s.get(Transaction, pending_id)
        if pending is None or not pending.expected:
            return []
        split_ids = set(s.exec(select(SplitShare.expense_txn_id)).all())
        q = (select(Transaction, Account.name).join(Account, Account.id == Transaction.account_id)
             .where(Transaction.account_id == pending.account_id, Transaction.direction == Direction.debit,
                    Transaction.expected == False))  # noqa: E712
        if nearby_only:
            window = timedelta(days=MATCH_DAYS)
            q = q.where(Transaction.amount_minor == pending.amount_minor,
                        Transaction.txn_date >= pending.txn_date - window,
                        Transaction.txn_date <= pending.txn_date + window)
        rows = [(t, acc) for t, acc in s.exec(q).all()
                if t.id not in split_ids and (txn_ids is None or t.id in txn_ids)]
    return sorted(rows, key=lambda r: (abs(r[0].amount_minor - pending.amount_minor),
                                       abs((r[0].txn_date - pending.txn_date).days), -r[0].txn_date.toordinal()))


def pending_matches(txn_ids: set[int]) -> list[tuple[Split, list[tuple[Transaction, str]]]]:
    """For a fresh import: pending splits that look like one of these new rows."""
    out = []
    for sp in splits():
        if sp.expected and (found := attach_candidates(sp.txn_id, txn_ids)):
            out.append((sp, found))
    return out


def attach_pending(pending_id: int, txn_id: int) -> None:
    """Move a pending split (shares and repayments) onto the imported
    transaction and drop the placeholder. The transaction takes the pending
    split's label (if it has none) and its description as a note."""
    with get_session() as s:
        pending = s.get(Transaction, pending_id)
        txn = s.get(Transaction, txn_id)
        if pending is None or not pending.expected:
            raise SplitError("That split is already attached.")
        if txn is None or txn.expected or txn.direction != Direction.debit:
            raise SplitError("Pick an imported expense.")
        if s.exec(select(SplitShare).where(SplitShare.expense_txn_id == txn_id)).first():
            raise SplitError("That transaction already has a split.")
        shares = list(s.exec(select(SplitShare).where(SplitShare.expense_txn_id == pending_id)))
        if sum(sh.amount_minor for sh in shares) > txn.amount_minor:
            raise SplitError(f"The shares add up to more than that transaction ({fmt(txn.amount_minor)}).")
        for row in shares + list(s.exec(select(ReimbursementLink)
                                        .where(ReimbursementLink.expense_txn_id == pending_id))):
            row.expense_txn_id = txn_id
            s.add(row)
        if txn.label_id is None:
            txn.label_id = pending.label_id
        if not txn.notes:
            txn.notes = pending.description
        s.add(txn)
        s.flush()
        s.delete(pending)
        s.commit()


def _paid_by_person(s, txn_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for link in s.exec(select(ReimbursementLink).where(col(ReimbursementLink.expense_txn_id).in_(txn_ids))):
        out[link.expense_txn_id][link.person] += link.amount_minor
    return out


def _linked_by_credit(s, credit_ids: list[int] | None = None) -> dict[int, int]:
    q = select(ReimbursementLink)
    if credit_ids is not None:
        q = q.where(col(ReimbursementLink.credit_txn_id).in_(credit_ids))
    out: dict[int, int] = defaultdict(int)
    for link in s.exec(q):
        out[link.credit_txn_id] += link.amount_minor
    return out


def record_repayment(expense_txn_id: int, credit_txn_id: int, person: str, amount_minor: int) -> None:
    """Link (part of) a bank credit to what `person` owes for an expense. The
    credit is marked as a reimbursement."""
    with get_session() as s:
        share = next((sh for sh in s.exec(select(SplitShare).where(SplitShare.expense_txn_id == expense_txn_id))
                      if sh.person.casefold() == person.casefold()), None)
        if share is None:
            raise SplitError(f"{person} isn't part of this split.")
        credit = s.get(Transaction, credit_txn_id)
        if credit is None or credit.direction != Direction.credit:
            raise SplitError("A repayment has to be money that came in.")
        if amount_minor <= 0:
            raise SplitError("The amount must be more than zero.")
        free = credit.amount_minor - _linked_by_credit(s, [credit_txn_id]).get(credit_txn_id, 0)
        if amount_minor > free:
            raise SplitError(f"Only {fmt(free)} of that credit is left to assign.")
        owing = share.amount_minor - _paid_by_person(s, [expense_txn_id])[expense_txn_id].get(share.person, 0)
        if amount_minor > owing:
            raise SplitError(f"{share.person} only owes {fmt(max(owing, 0))} more for this.")
        s.add(ReimbursementLink(expense_txn_id=expense_txn_id, credit_txn_id=credit_txn_id,
                                amount_minor=amount_minor, person=share.person))
        credit.kind = TxnKind.reimbursement
        credit.kind_manual = True  # you linked it yourself
        s.add(credit)
        s.commit()


def remove_repayment(link_id: int) -> None:
    with get_session() as s:
        if link := s.get(ReimbursementLink, link_id):
            s.delete(link)
            s.commit()


def splits() -> list[Split]:
    """Every split expense, newest first."""
    with get_session() as s:
        shares = list(s.exec(select(SplitShare)))
        links = [ln for ln in s.exec(select(ReimbursementLink)) if ln.expense_txn_id is not None]
        ids = {sh.expense_txn_id for sh in shares} | {ln.expense_txn_id for ln in links}
        if not ids:
            return []
        rows = s.exec(select(Transaction, Account.name, Label.name)
                      .join(Account, Account.id == Transaction.account_id)
                      .join(Label, Label.id == Transaction.label_id, isouter=True)
                      .where(col(Transaction.id).in_(ids))).all()
        credits = {t.id: (t, acc) for t, acc in s.exec(
            select(Transaction, Account.name).join(Account, Account.id == Transaction.account_id)
            .where(col(Transaction.id).in_({ln.credit_txn_id for ln in links})))}
    out = {t.id: Split(t.id, t.txn_date, t.description, acc, lab or "", t.amount_minor, expected=t.expected,
                       account_id=t.account_id) for t, acc, lab in rows}
    for sh in shares:
        out[sh.expense_txn_id].people.append(PersonShare(sh.person, sh.amount_minor))
    for ln in links:
        sp = out[ln.expense_txn_id]
        credit, acc = credits[ln.credit_txn_id]
        sp.repayments.append(Repayment(ln.id, credit.id, credit.txn_date, acc, ln.person, ln.amount_minor))
        person = next((p for p in sp.people if p.person.casefold() == ln.person.casefold()), None)
        if person is None:  # a link made before shares existed
            person = PersonShare(ln.person, 0)
            sp.people.append(person)
        person.paid_minor += ln.amount_minor
    for sp in out.values():
        sp.repayments.sort(key=lambda r: r.credit_date)
    return sorted(out.values(), key=lambda sp: (sp.txn_date, sp.txn_id), reverse=True)


def people() -> list[str]:
    """Names used before, for autocomplete."""
    with get_session() as s:
        names = {sh.person for sh in s.exec(select(SplitShare))}
        names |= {ln.person for ln in s.exec(select(ReimbursementLink)) if ln.person}
    return sorted(names, key=str.casefold)


def _matches(text: str, query: str) -> bool:
    words = query.casefold().replace(",", "").split()
    return all(w.lstrip("₹") in text for w in words)


def expense_candidates(query: str = "", limit: int = 60) -> list[tuple[Transaction, str]]:
    """Money you paid out that could be split, newest first."""
    with get_session() as s:
        rows = s.exec(select(Transaction, Account.name).join(Account, Account.id == Transaction.account_id)
                      .where(Transaction.direction == Direction.debit, Transaction.kind == TxnKind.expense,
                             Transaction.expected == False)  # noqa: E712
                      .order_by(col(Transaction.txn_date).desc(), col(Transaction.id).desc())).all()
    out = []
    for t, acc in rows:
        hay = f"{t.description} {merchant_name(t.description)} {acc} {t.amount_minor / 100:.2f}".casefold()
        if not query or _matches(hay, query):
            out.append((t, acc))
            if len(out) >= limit:
                break
    return out


def credit_candidates(query: str = "", limit: int = 60) -> list[Credit]:
    """Money that came in and isn't fully assigned yet (card payments and
    merchant refunds aside), newest first."""
    with get_session() as s:
        rows = s.exec(select(Transaction, Account.name).join(Account, Account.id == Transaction.account_id)
                      .where(Transaction.direction == Direction.credit,
                             col(Transaction.kind).not_in([TxnKind.cc_payment, TxnKind.refund]))
                      .order_by(col(Transaction.txn_date).desc(), col(Transaction.id).desc())).all()
        linked = _linked_by_credit(s)
    out = []
    for t, acc in rows:
        c = Credit(t.id, t.txn_date, acc, t.description, t.kind, t.amount_minor, linked.get(t.id, 0))
        hay = f"{t.description} {c.name} {acc} {t.amount_minor / 100:.2f}".casefold()
        if c.free_minor > 0 and (not query or _matches(hay, query)):
            out.append(c)
            if len(out) >= limit:
                break
    return out


def unassigned_reimbursements() -> list[Credit]:
    """Credits marked as reimbursements that aren't (fully) linked to a split."""
    return [c for c in credit_candidates(limit=10_000) if c.kind == TxnKind.reimbursement]
