# Rinz, once: the purchases agreed on 6 Nov 2026 into the accounts. Run through
# `odoo shell`. MODE=preview changes nothing; MODE=bills posts the bills;
# MODE=close posts the Stock Closing. It stops if the figures are not the agreed ones.
import os

MODE = os.environ.get("MODE", "preview")
BANK = "Prime Bank Limited"
PETTY = {"P00151", "P00160", "P00184", "P00205", "P00219"}
ORDERS, TOTAL = 178, 26719209.35
Book = env["odin.stock.book"]


def show(title, data):
    print("\n== " + title)
    for key, value in data.items():
        print("   %s: %s" % (key, value))


def stop(why):
    print("\n!! STOPPED, nothing more was posted: " + why)
    raise SystemExit(1)


report = Book.book_bill_imported(dry_run=True)
petty = [p for p in report["petty"]]
billed = env["account.move"].search_count([("move_type", "=", "in_invoice"), ("state", "=", "posted")])
show("purchases", {"orders without a bill": report["orders"], "to bill now": report["to_bill"],
                   "total to bill": round(report["to_bill_total"], 2), "left out, part-imported": len(report["partial"]),
                   "left out total": round(report["partial_total"], 2), "bills already posted": billed})
print("\n== paid from petty cash")
for p in petty:
    print("   %(order)s %(supplier)s %(date)s %(amount)s <- petty %(petty_id)s %(petty_date)s %(description)s" % p)
journal = env["account.journal"].search([("name", "=", BANK), ("company_id", "=", env.company.id)])
if len(journal) != 1:
    stop("no single journal called " + BANK)
found = {p["order"] for p in petty}
if MODE != "close" and not billed:
    if report["to_bill"] != ORDERS or abs(report["to_bill_total"] - TOTAL) > 0.01:
        stop("expected %s orders, KES %s" % (ORDERS, TOTAL))
    if found != PETTY:
        stop("petty cash matches differ from the 5 agreed: %s" % sorted(found ^ PETTY))

if MODE == "bills":
    show("ledger setup", Book.book_ledger_setup(dry_run=False))
    env.cr.commit()
    pairs = [[p["order_id"], p["petty_id"]] for p in petty if p["order"] in PETTY]
    orders = Book._imported_orders().filtered(lambda o: "PARTIAL" not in (o.origin or ""))
    done, failed = 0, []
    for start in range(0, len(orders), 20):
        batch = orders[start:start + 20]
        result = Book.book_bill_imported(dry_run=False, order_ids=batch.ids, petty_pairs=pairs, pay_journal_id=journal.id)
        env.cr.commit()
        done += len(result["billed"])
        failed += result["failed"]
        print("   billed %s of %s" % (done, len(orders)), flush=True)
    show("bills", {"billed": done, "failed": len(failed)})
    for row in failed:
        print("   FAILED %(order)s: %(why)s" % row)
elif MODE == "close":
    if Book.book_bill_imported(dry_run=True)["to_bill"]:
        stop("some orders are still not billed: run MODE=bills again")
    closing = Book.book_stock_closing(dry_run=False)
    env.cr.commit()
    show("closing", {k: v for k, v in closing.items() if k != "lines"})

closing = Book.book_stock_closing(dry_run=True)
show("stock closing" + (" now" if MODE == "close" else ", would post"),
     {"stock value": round(closing["stock_value"], 2), "ledger value": round(closing["ledger_value"], 2)})
for line in closing["lines"]:
    print("   %-42s Dr %14.2f  Cr %14.2f  %s" % (line["account"], line["debit"], line["credit"], line["name"]))
if MODE == "preview":
    show("ledger setup, would do", Book.book_ledger_setup(dry_run=True))
    admin = env.ref("base.user_admin")
    show("moves not on the book, not touched", Book.with_user(admin).book_log_missing(dry_run=True))
print("\nDone, mode " + MODE)
