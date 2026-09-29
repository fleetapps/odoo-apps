# Gymkhana POS Day Import (Odoo 19)

Posts one day of Nairobi Gymkhana bar sales from the POS **Group Sales Register**
PDF: one sale order and one delivery per bar (stock taken from the bar's own
location, kits exploded into their components) and **one invoice** whose total
equals the PDF's grand total to the cent.

It lives in **Bar Control**, next to the bars and their counts: *POS import →
Import a day / Imported days*, and *Configuration → Bars / POS import settings
/ POS item mapping*. The bars are those of Bar Control (`odin_bar_base`): a
bar with a POS group name is a bar of the report. The documents it creates are
ordinary Sales, Inventory and Invoicing documents, so they also show in those
apps as usual.

## Flow

1. **Drop the PDF.** It is read and checked at once; any problem comes back as one
   sentence (e.g. *Sub group Beer/RTD/0.0/Cans(BE): lines add up to 56,216.18 but
   the report says 56,261.18.*).
2. **Review.** Header (date · total · lines) and a card per bar; red banner for POS
   items without a product, fixed inline (every choice is saved to the item
   mapping); *Money* tab (invoice total = PDF total, VAT and CTL, the rounding
   difference with the PDF's tax for information); a tab per bar with the stock
   deducted (after kits) and the bar's stock before → after (orange when it goes
   negative, not blocking); the records to be created. **Post day** stays disabled
   while anything is red.
3. **Done.** Links to the orders, deliveries and invoice; the stock outcome as
   posted; with the Bar Desk, the bar counts the day made ready to approve.

**Undo day** (administrators, while the invoice is unpaid): returns the deliveries,
credits the invoice, cancels the orders; the date can then be imported again.
With the Bar Desk, counts approved on that day's sales go back for approval
(the screen lists them first).

## Hard blocks

Not a Group Sales Register (header row not found) · more than one day · another
outlet · a future or accounting-locked date · any line or total that doesn't add
up (0.05 tolerance on amount/discount/tax, none on net) · a group that isn't
configured or an item whose suffix doesn't match its group · a bar of the
report without its POS suffix, POS sales type, delivery contact or analytic
account · an unmatched item,
or one mapped to an archived or other-company product · a date or file already
imported.

Warnings (not blocking): negative stock after the day · a PDF price different
from the Odoo list price · a configured bar with no sales · a quantity that
doesn't agree with rate × quantity = amount on the PDF.

## Posting (one transaction, all or nothing)

The import row is locked (`SELECT … FOR UPDATE`) and every check is re-run
against the stored PDF. Then, per bar: a sale order (customer = the club,
shipping = the bar's contact, date = business date, analytic = the bar,
price = Net ÷ Qty with the configured taxes, plus a "POS rounding" line when a
Net can't be split exactly). The deliveries are created with the bar's SAL
operation type and the bar as source **before any reservation** (so kit
components come off the bar too), validated and dated 23:59 Nairobi time. One
invoice is created from the three orders, dated the business date, posted, and
checked against the PDF total. Any error rolls everything back; the import stays
in review.

Last, the day is recorded in Bar Control: every delivery carries the trading
day (`bar_business_date`) and every bar of the company's report gets a POS day
(*No sales* for a bar without a line). Undo returns the deliveries on the same
trading day and removes those POS days. See the `odin_bar_base` README.

Odoo 19 groups invoices by shipping address, so the day's invoice is addressed to
the club. Each bar's lines are tax-rounded on their own, so every bar's revenue
(analytic) and tax on the invoice are exactly those of its order.

## Installation

1. Add `pdfplumber==0.11.9` to the Odoo image (see `requirements.txt` at the repo
   root). Do **not** use 0.11.10: it requires Pillow ≥ 12.2, which conflicts with
   Odoo 19's Pillow. The parser has no other dependency and no Odoo import.
   On Python 3.11 images, pdfplumber's pdfminer needs a newer `cryptography`
   than Odoo pins there: use Odoo's Python 3.12 pins (`cryptography==42.0.8`,
   `pyopenssl==24.1.0`, `urllib3==2.0.7`).
2. Install the app; it brings Bar Base (`odin_bar_base`). On a database with the
   company *Dhostana Ventures Ltd*, the install pre-fills the settings, the POS
   setup of the three bars and the item mapping from
   `data/pos_item_map_seed.csv` (only rows whose product id and name match).
   Rows marked *NEEDS PRODUCT / NEEDS KIT / NEEDS DECISION* in the CSV need the
   product created first; they then show up as unmatched in the first reviews.
3. Give users *POS Day Import / User* (import and post) or *Administrator* (also
   configuration and undo).

**Upgrading from 19.0.1.0.0** (the version with its own bar list): update the
module. Odoo installs Bar Base first; the upgrade turns each import bar into a
Bar Control bar (or completes the one already there for the same location),
keeping its location, SAL type, delivery contact, analytic account, POS group
and suffix, and fills its ISS/RET types from the stock names. Import lines and
orders follow. Days already posted get their trading day and POS days.

## Tests

```bash
# parser only, no Odoo needed
python -m unittest discover -s gymkhana_pos_import/tests -p 'test_parser.py'
# everything, in a test database
odoo-bin -d <test_db> -i gymkhana_pos_import --test-tags /gymkhana_pos_import --stop-after-init
# end to end on a COPY of production (rolled back; skipped with the reason if not ready)
odoo-bin -d <copy_of_prod> --test-tags pos_import_livecopy --stop-after-init
```

Before go-live, add 10–15 more real PDFs to `tests/fixtures/` (a slow day, a day
with a discount, a day with a bar closed): `TestAllFixtures` checks every one.
