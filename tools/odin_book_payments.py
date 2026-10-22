"""Book the supplier payments Odoo left out of the books, each against its bill.

Odoo 19 writes a payment's journal entry only when its payment method has an
outstanding account. A bank or M-Pesa journal created by hand has none, so a
payment from it waits "in process" for a bank statement to match it -- and a
bar that pays its suppliers by M-Pesa never imports one. The payment never
reaches the ledger, the bill reads "Not paid", and the balance sheet goes on
owing suppliers who were paid. Bar Desk payments made before the Desk booked
them itself are all like this.

This gives each such payment its entry, to Outstanding Payments as Odoo does
when it books a payment itself, and settles the bill it paid:

  1. the bill the payment is already linked to;
  2. else the supplier's one open bill for exactly that amount dated the day
     it was paid;
  3. else the supplier's one open bill for exactly that amount.

A payment with no such bill is still booked, and stays open on the supplier's
account as money paid ahead; the report lists it.

Run on the instance, as root (the odoo container's own shell):

    docker exec -i odoo odoo shell -c /etc/odoo/odoo.conf -d <db> --no-http \\
        < tools/odin_book_payments.py

It runs every change and rolls it back, printing what it would do. With
-e APPLY=1 after `docker exec -i` it keeps them. -e COMPANY=<name> limits it to
one company; -e OUTSTANDING=<code> books to that account instead of the one
Odoo would pick. Booking gives a payment the journal's numbering, so the report
shows its old name beside the new one.
"""

import os

from odoo import Command
from odoo.tools import float_round

APPLY = os.environ.get("APPLY") == "1"
COMPANY = os.environ.get("COMPANY")
OUTSTANDING = os.environ.get("OUTSTANDING")

companies = env["res.company"].search([("name", "=", COMPANY)] if COMPANY else [])
if not companies:
    raise SystemExit(f"No company named {COMPANY!r}")
env = env(context=dict(env.context, allowed_company_ids=companies.ids))
log = []


def say(line=""):
    log.append(str(line))


def money(value):
    return f"{float_round(value, precision_digits=2):>13,.2f}"


def owed(company):
    """What the books say is owed to suppliers."""
    groups = env["account.move.line"]._read_group(
        [
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "=", "liability_payable"),
        ],
        [],
        ["balance:sum"],
    )
    return -(groups[0][0] or 0.0)


def the_bill(payment, taken):
    linked = payment.invoice_ids.filtered(
        lambda move: move.state == "posted" and move.is_purchase_document(True) and move not in taken
    )
    if len(linked) == 1:
        return linked, "linked"
    open_bills = env["account.move"].search([
        ("company_id", "=", payment.company_id.id),
        ("move_type", "=", "in_invoice"),
        ("state", "=", "posted"),
        ("commercial_partner_id", "=", payment.partner_id.commercial_partner_id.id),
        ("amount_residual", "!=", 0),
        ("id", "not in", list(taken.ids)),
    ])
    same = open_bills.filtered(lambda bill: bill.currency_id.compare_amounts(bill.amount_residual, payment.amount) == 0)
    same_day = same.filtered(lambda bill: bill.invoice_date == payment.date)
    if len(same_day) == 1:
        return same_day, "same amount, same day"
    if len(same) == 1:
        return same, "same amount"
    return env["account.move"], f"{len(same)} open bills for that amount" if same else "no open bill for that amount"


for company in companies:
    payments = env["account.payment"].search([
        ("company_id", "=", company.id),
        ("state", "=", "in_process"),
        ("move_id", "=", False),
        ("partner_type", "=", "supplier"),
        ("payment_type", "=", "outbound"),
    ], order="date, id")
    if not payments:
        continue
    before = owed(company)
    say(f"== {company.name}: {len(payments)} supplier payments with no journal entry")
    say()
    chosen = env["account.account"]
    if OUTSTANDING:
        chosen = env["account.account"].search(
            [("company_ids", "in", company.id), ("code", "=", OUTSTANDING)], limit=1
        )
        if not chosen.reconcile:
            raise SystemExit(f"{company.name} has no reconcilable account {OUTSTANDING}")
    taken = env["account.move"]
    accounts = env["account.account"]
    settled = 0.0
    for payment in payments:
        old_name = payment.name
        bill, why = the_bill(payment, taken)
        taken |= bill
        account = (
            chosen
            or payment.payment_method_line_id.payment_account_id
            or payment._get_outstanding_account(payment.payment_type)
        )
        accounts |= account
        payment.outstanding_account_id = account
        # Writing the state is what makes Odoo create and post the entry.
        payment.write({"state": "in_process"})
        if bill:
            payable = payment.destination_account_id
            (payment.move_id.line_ids + bill.line_ids).filtered(
                lambda line, payable=payable: line.account_id == payable and not line.reconciled
            ).reconcile()
            bill.matched_payment_ids = [Command.link(payment.id)]
            settled += payment.amount
        say(
            f"   {payment.date} {old_name:<10} {payment.name:<18} {payment.partner_id.name[:20]:<20} "
            f"{money(payment.amount)}  "
            + (f"-> {bill.name} ({bill.ref or 'no ref'}), {why}: {bill.payment_state}" if bill
               else f"booked, left open as paid ahead: {why}")
        )
    say()
    say(f"   booked to {', '.join(accounts.mapped('display_name'))}; bills settled for {money(settled).strip()}")
    say(f"   owed to suppliers: {money(before).strip()} -> {money(owed(company)).strip()}")
    bare = env["account.journal"].search([
        ("company_id", "=", company.id), ("type", "in", ("bank", "cash")),
    ]).outbound_payment_method_line_ids.filtered(lambda line: not line.payment_account_id)
    if bare:
        say()
        say("   Payment methods with no outstanding account (payments made by hand from them")
        say("   wait for a bank statement before they reach the books):")
        for line in bare:
            say(f"     - {line.journal_id.name}: {line.name}")
    say()

if not log:
    say("Every supplier payment is already in the books.")
if APPLY:
    env.cr.commit()
    say("committed.")
else:
    env.cr.rollback()
    say("rolled back; nothing was written (add -e APPLY=1 to keep it).")
print("\n".join(log))
