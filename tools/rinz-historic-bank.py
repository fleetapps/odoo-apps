# Rinz, once: the 173 historic bills paid from Prime Bank, settled against the
# bank account, so the bills read Paid rather than In Payment. There are no
# statements to match them with. Run through `odoo shell`. MODE=preview tries the
# first payment and rolls it back; MODE=post settles them all.
import os

MODE = os.environ.get("MODE", "preview")
BANK, COUNT, TOTAL = "Prime Bank Limited", 173, 26669329.35
journal = env["account.journal"].search([("name", "=", BANK), ("company_id", "=", env.company.id)])
if len(journal) != 1:
    raise SystemExit("!! STOPPED: no single journal called " + BANK)
misc = env["account.journal"].search([("code", "=", "MISC"), ("company_id", "=", env.company.id)], limit=1)
payments = env["account.payment"].search([("journal_id", "=", journal.id), ("state", "=", "in_process")], order="date, id")
total = round(sum(payments.mapped("amount")), 2)
print("\n== payments in process: %s, KES %s" % (len(payments), total))
done = env["account.payment"].search_count([("journal_id", "=", journal.id), ("state", "=", "paid")])
if not done and (len(payments) != COUNT or abs(total - TOTAL) > 0.01):
    raise SystemExit("!! STOPPED: expected %s payments, KES %s" % (COUNT, TOTAL))
bank = journal.default_account_id


def settle(payment):
    liquidity, _counterpart, _writeoff = payment._seek_for_lines()
    outstanding = liquidity.account_id
    if len(outstanding) != 1 or not outstanding.reconcile or outstanding == bank:
        raise SystemExit("!! STOPPED: %s is not on an outstanding account" % payment.name)
    bill = payment.reconciled_invoice_ids
    name = "Historic bank payment %s, %s" % (payment.name, bill[:1].name or "")
    entry = env["account.move"].create({
        "move_type": "entry", "journal_id": misc.id, "date": payment.date, "ref": name,
        "line_ids": [
            (0, 0, {"account_id": outstanding.id, "partner_id": payment.partner_id.id, "name": name,
                    "debit": payment.amount, "credit": 0.0}),
            (0, 0, {"account_id": bank.id, "partner_id": payment.partner_id.id, "name": name,
                    "debit": 0.0, "credit": payment.amount}),
        ],
    })
    entry.action_post()
    (entry.line_ids.filtered(lambda l: l.account_id == outstanding) | liquidity).reconcile()
    env.flush_all()
    payment.invalidate_recordset()
    bill.invalidate_recordset()
    return bill


if MODE == "preview":
    payment = payments[:1]
    bill = settle(payment)
    print("   tried %s: payment %s, matched %s; bill %s %s" % (
        payment.name, payment.state, payment.is_matched, bill.name, bill.mapped("payment_state")))
    env.cr.rollback()
    print("   rolled back: nothing changed")
elif MODE == "post":
    bad = 0
    for index, payment in enumerate(payments, 1):
        bill = settle(payment)
        if payment.state != "paid" or set(bill.mapped("payment_state")) != {"paid"}:
            bad += 1
            print("   NOT PAID %s %s %s" % (payment.name, payment.state, bill.mapped("payment_state")))
        if not index % 20:
            env.cr.commit()
            print("   settled %s of %s" % (index, len(payments)), flush=True)
    env.cr.commit()
    print("   settled %s, not paid %s" % (len(payments), bad))
print("Done, mode " + MODE)
