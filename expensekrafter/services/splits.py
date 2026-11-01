"""Shared expenses: who owes you what for an expense you paid, and the money
they've sent back.

A split is an expense with one SplitShare per person. Repayments are bank
credits linked to the expense with a ReimbursementLink (one credit can cover
several splits or people, one person can pay in parts). Spend analysis takes
linked repayments off the expense itself, so only your share counts as spend
once everyone has paid.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

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

    @property
    def name(self) -> str:
        return merchant_name(self.description)

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
    they arrived."""
    with get_session() as s:
        for row in s.exec(select(SplitShare).where(SplitShare.expense_txn_id == expense_txn_id)):
            s.delete(row)
        for row in s.exec(select(ReimbursementLink).where(ReimbursementLink.expense_txn_id == expense_txn_id)):
            s.delete(row)
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
    out = {t.id: Split(t.id, t.txn_date, t.description, acc, lab or "", t.amount_minor) for t, acc, lab in rows}
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
                      .where(Transaction.direction == Direction.debit, Transaction.kind == TxnKind.expense)
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
