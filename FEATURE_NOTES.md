# ExpenseKrafter: feature notes

What the app does and the real-life situation each feature was built for. The
guiding rule throughout: **show what you actually spent, and never count the
same money twice.** A card bill payment is a transfer (the card purchases are
the spend), money paid back to you reduces the expense it pays for, and the
same statement row imported twice is still one transaction.

For setup, builds and where data lives, see the [README](README.md).

---

## Getting data in

### Statement import with duplicate detection
**Scenario:** your money is spread over several bank accounts and credit cards,
each with its own export (PDF, XLSX, XLS, CSV), and the exports overlap: this
month's download repeats last week's rows.

- Generic Excel/CSV and PDF-table parsers, plus bank-specific ones (SBI bank and
  SBI Card statements). Password-protected PDFs and Excel files ask for the
  password, which can be remembered per account in the OS credential store.
- Every row gets a fingerprint (bank reference, or date + amount + direction +
  description + running balance), so overlapping or re-imported files never
  create duplicates.
- **Same row, different wording:** two exports of the same statement (e.g. an
  emailed statement and a net-banking download) can space or cut off the
  description differently. A bank row with the same date, amount, direction and
  balance after it as one already imported is treated as a duplicate whatever
  its description says.
- **Selective import:** a preview lists every row as new / already imported /
  skipped before. Untick rows you don't want; they stay unticked next time.
  Card bill payments can be unticked in one click, since they're usually already
  in the bank statement.

### Description rules
**Scenario:** bank narrations are cryptic (`WDL TFR UPI/DR/1234/MERCHANT/...`).

- Regex rules turn raw text into a clean name, a method (UPI, NEFT, Card...),
  detail "bubbles", and a default type (income, card payment, refund...).
- A personal rules file (`my_rules.toml` in the settings folder) holds your own
  rules and aliases, kept out of the shared defaults and the repo.
- Settings → Description rules: test a description and see which rule matched.

### Import history and "where each account is up to"
**Scenario:** you can't remember which statements you've loaded or which
account is behind.

- The Import page lists every imported file (when, account, dates covered,
  rows added / already there).
- A card per account shows its latest transaction, how long ago it was, the
  balance after it and the last import, with a **Due for an import** warning
  when nothing is newer than 35 days.

### Send to → ExpenseKrafter, and the Windows Share sheet
**Scenario:** statements arrive on WhatsApp or email; downloading and then
finding the file in the app's upload box is tedious.

- Right-click a statement in Explorer → **Send to → ExpenseKrafter** (added from
  Settings), or pick ExpenseKrafter in the Windows **Share** window.
- The file lands in a "Sent to ExpenseKrafter" list on the Import page. An open
  window comes forward and switches to Import; a closed app opens there.
- The Share sheet needs a package identity: `packaging/windows/share_target.py`
  builds a signed sparse package for the installed app (one-time trust of a
  self-signed certificate) and re-registers it after each install.

---

## Organising transactions

### Transactions page
**Scenario:** hundreds of rows a month need labels, types and notes.

- Month → day accordion with inline type, label and notes on every row.
- Search (name, details, notes, label, amount) and filters (account, type,
  label, direction, date range, amount range, unlabelled only).
- The search / filter / group toolbar stays pinned while the list scrolls.

### Labels, recurring labels and label groups
**Scenario:** you want categories, some of which repeat (rent, subscriptions,
a half-yearly insurance), and some that are the same thing at a higher level
(a monthly SIP and one-time investments are both "Investments").

- Labels with colours; recurring labels with any interval (weekly, monthly,
  every 6 months...) power the subscriptions/recurring view.
- **Label groups:** a label can sit under a group label. The dashboard counts it
  as the group; breakdowns and transaction lists still show the label itself.
  Filtering by a group includes its labels.
- On the Labels page, **drag** a label onto another to group it, or onto the
  box above the list to ungroup it.

### Expense groups
**Scenario:** several transactions are really one thing: a purchase paid in two
parts because of a transfer limit, a trip, an event.

- Make a group up front (e.g. "Trip, Nov 2026"); it lasts until you delete it.
- On Transactions, tick rows → **Add to group ▾** (an existing group or a new
  one), or **Remove from group**.
- The **Expense groups** page shows each group's transactions, dates, total and
  label, and its reimbursement status when it has one ("Expense report · claimed
  · received · to come").
- **Card expense not imported yet:** add a card payment to a group before its
  statement arrives; it's matched to the real row when the statement is imported.
- **Split / reimburse this group** starts a split for the whole group.

---

## Money that comes back to you

### Splits & reimbursements
**Scenario:** you paid for friends, or your company pays you back, and you want
to know who still owes what, without the full amount counting as your spend.

- Split an expense (or a whole expense group): say who pays you back and how
  much. Record repayments as the money arrives in the bank, in parts if needed.
- Spend counts only your share: repayments come off the expense they're for
  (spread over a group's transactions in proportion to their amounts).
- Credits marked as reimbursements but not linked yet are listed so they can be
  linked to the right expense.
- The split list folds away; its header keeps "₹X to come from ..." in view.

### Pending splits (card statement not in yet)
**Scenario:** you paid by card, a friend paid you back the same week, but the
card statement only comes weeks later. Waiting means forgetting the details.

- **New split → "Card statement not in yet":** record the card, date, amount,
  what it was for and who owes what on the day. Repayments can be linked at once.
- It never counts as spend on its own, and linked repayments are held aside
  until it's matched. Importing the card statement offers to attach it to the
  row with the same amount within 7 days; it can also be attached by hand.
- A pending split older than 45 days is flagged.

### Reimbursements paid inside the salary
**Scenario:** your employer pays back part of an expense (a meal allowance, an
internet bill) inside the salary credit, so there's no separate credit to mark.

- Splits & reimbursements → **Reimbursed in your salary:** enter the amount and
  the label it pays back.
- It comes off spend for that label **and** off income, so it isn't counted as
  both earnings and a saving. "Add again for the next month" repeats it.

---

## Credit cards

### Card statements and bill tracking
**Scenario:** you always pay the card in full after the statement, and want to
know what's due, when, and whether it's paid.

- Card statements (imported from the card PDF, or entered by hand) with total
  and minimum due and the due date.
- Link the bank debits that paid a bill (several debits per bill, or one debit
  across bills). Paying the card counts as a transfer, never as spend.
- Status per bill (pending / partially paid / overdue / paid), "Card bills
  pending" on the dashboard, and desktop due-date reminders.

### Mark as paid (bank statement not in yet)
**Scenario:** you paid the bill, but the bank statement with the payment isn't
imported yet, so the bill still looks unpaid.

- **Mark as paid:** the account it was paid from (defaults to the one that paid
  this card before), the date, the amount and a note (e.g. "via CRED").
- The bill shows **✓ Marked paid** with "waiting for bank statement". Importing
  that bank statement offers to attach it to the real debit (same amount within
  7 days); **Attach** does it by hand, **Undo** removes it.

---

## Seeing where the money goes

### Dashboard
**Scenario:** one place to see what you spend, how it's changing, and where.

- **Period:** presets (this month, last month, 3M, 6M, 12M, YTD, all) or any
  single month; an account filter.
- **Headline:** spent, change vs the comparable previous period, income, kept
  (savings rate), bank balance, biggest label, card bills pending, unlabelled.
- **Spend over time:** stacked by label; click a bar to see its transactions.
- **Where it went:** top 5 labels plus Miscellaneous, bar or pie, with a full
  breakdown (including labels inside label groups).
- **Cash flow** (income vs spend vs kept) and **bank balance** over time,
  starting from each account's opening balance.
- **What changed:** labels that went up or down vs the previous period.
- **Spending calendar:** spend per day as a heatmap; click a day to see its
  transactions.
- **This month vs last month** with an "at this pace" month-end projection,
  **top merchants**, **largest expenses**, **spend by account**, **recurring**.

### Hide amounts
**Scenario:** you open the app with someone looking at your screen.

- The eye button in every page header replaces every amount with `₹∗∗∗∗`
  (text, tables, chart axes, labels and tooltips), in place, without reloading
  or losing your search. Percentages and counts stay. Remembered per window.

---

## App, data and privacy

### Desktop, web and Docker
- **Desktop app** (Windows build installed to Program Files; Linux tarball with
  an installer). **Web mode** (`expensekrafter --web`) for any OS, including
  Wayland desktops; a **Docker** image keeps all data in one mounted folder.
- Light and dark themes.

### Your data stays yours
- One SQLite file in a folder you choose; point it at Google Drive for Desktop
  to sync. A lock file warns if another PC has it open, and a backup is taken
  on every launch.
- **App password:** asked at every start (desktop and web); only a salted
  scrypt hash is stored. Required before serving the web app beyond localhost.
- **Statement passwords** live in the OS credential store, one per account,
  never in the database; **Forget password** on Accounts removes one.
- Account and card numbers are stored only as the first 5 characters.
- Nothing personal ships with the app or the repo: tests use made-up data.
