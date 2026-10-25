# Kenya eTIMS for Odoo 19 Community

KRA eTIMS e-invoicing for Kenyan companies, in five modules. Companies of any
other country in the same database are not affected: their invoices post,
print and send as before, and their users do not see the eTIMS columns,
filters or menus.

| Module | Depends on | What it adds |
|---|---|---|
| `x_ke_etims` | `account`, `l10n_ke`, `x_ke_base` | Devices, KRA codes, the signing queue, invoices and credit notes, reverse invoices, invoice PDF block, four backends |
| `x_ke_etims_pos` | `x_ke_etims`, `point_of_sale` (auto) | POS orders signed at payment, KRA signature and QR code on the receipt, "eTIMS: pending" fallback |
| `x_ke_etims_stock` | `x_ke_etims`, `stock`, `purchase` | Stock movements and stock master, supplier invoices from eTIMS to draft bills, customs imports |
| `x_ke_etims_mrp` | `x_ke_etims_stock`, `mrp` (auto) | Item compositions from bills of materials; production as stock movements |
| `x_ke_etims_vat` | `x_ke_etims`, `x_ke_vat` (auto) | eTIMS numbers in the iTax reconciliation and the VAT drill-down; bills without an eTIMS invoice |

All five are LGPL-3. Note that `x_ke_base` and `x_ke_vat` are OPL-1.

## Installing

```
pip install requests qrcode
pip install etims        # optional: only for the "KRA OSCU via etims library" backend
```

Install `x_ke_etims`. The POS, MRP and VAT modules install themselves when
their other dependency is present. `x_ke_etims_stock` is installed by hand.

## Setting up

1. **Settings > Accounting > Kenya eTIMS.** Choose the default backend and
   environment, the company's KRA branch (`00` for head office) and the default
   payment type, and turn on *Send on posting*.
2. **Accounting > Configuration > Kenya eTIMS > Devices.** Create one device
   per KRA branch with its PIN, its branch ID, and either the device serial
   (OSCU) or the API key (DigiTax). Then press **Initialise**. Use **Sync KRA
   codes** for the code lists and item classes. DigiTax has no code-list API,
   so the module ships a seed of the codes it needs.
3. **Products.** On the *Invoicing* tab, give each product a KRA item class,
   a product type, and its packaging and quantity units. The KRA item code is
   generated, and the product is registered with the device on first use.
4. **Taxes.** Each Kenyan VAT tax gets its KRA tax type (A Exempt, B 16%,
   C 0%, D Non-VAT, E 8%) from Kenya Tax Base's supply classification, so a
   line transmits under the same band its VAT3 box reports. The tax type can
   be changed on the tax.

Production devices need a certification reference, KRA's approval of the
integration, before they can be saved.

## Backends

| Operation | Demo | KRA OSCU (direct) | KRA OSCU (etims lib) | DigiTax |
|---|---|---|---|---|
| Initialise, branches, notices | yes | yes | yes | yes |
| Code lists, item classes | yes | yes | yes | seeded |
| Item registration | yes | yes | yes | yes |
| Sales, credit notes | yes | yes | yes | yes |
| Status polling, callbacks | - | - | - | yes |
| Purchases (fetch and confirm) | yes | yes | yes | yes |
| Stock movements | yes | yes | yes | transfers and adjustments |
| Stock master | yes | yes | yes | - (DigiTax keeps stock) |
| Customs imports | yes | yes | yes | yes |
| Item compositions | yes | yes | - | - |
| Reverse invoices | - | - | - | yes |

- **KRA OSCU (direct)** talks to `https://etims-api-sbx.kra.go.ke/etims-api`
  (sandbox) and `https://etims-api.kra.go.ke/etims-api`.
- **KRA OSCU via etims library** makes the same calls through the MIT
  `etims` package. It is marked experimental and reports itself unavailable
  when the package is not installed.
- **DigiTax** talks to `https://api.digitax.tech/ke/v2` with the `X-API-Key`
  header, using a TEST or LIVE key. DigiTax does not sign its notification
  callbacks. The callback URL carries a per-device secret token, and the
  module then re-reads the sale from DigiTax: the callback body itself is
  never trusted.
- **Demo** produces deterministic fake receipts. Every document is
  watermarked "DEMO - NOT A TAX INVOICE".

## How signing works

- **Posting queues; it never signs.** A queue job runs every minute.
  "Send to eTIMS" on the invoice, and sending the invoice by email, make one
  quick attempt (about 4 seconds) first. Whatever is still unsigned stays in
  the queue.
- **KRA invoice numbers are drawn at send time**, under a lock on the device,
  and saved before the request leaves. A retry reuses the same number. A
  number is released only when KRA definitely did not receive the request
  and no later number has been drawn. On KRA OSCU, which requires numbers in
  sequence, a document that holds a number blocks the ones after it, and the
  device names that document.
- **Retries** back off exponentially (from 60 s up to an hour, with jitter).
  After 8 failed attempts the document is marked failed and the salesperson
  gets an activity. A rejection that cannot succeed on retry fails at once.
- **Before anything is sent**, the invoice is checked: one VAT tax per line
  with a KRA tax type, registered products, a valid PIN, a signed original
  for a credit note, and a KRA reason code on credit notes.
- **A signed invoice cannot be reset to draft.** Correct it with a credit
  note. An invoice PDF printed before signing says "Not yet signed by
  eTIMS", and is regenerated once the invoice is signed.

## Everyday sales flows

- **Discounts, down payments, loyalty rewards.** KRA has no negative lines.
  A negative line (the Sales "Discount" button, a down payment deducted on
  the final invoice, a coupon or loyalty reward, a POS discount) is spread
  as discount over the lines of the same VAT rate, so KRA gets exactly the
  invoice total. A deduction larger than the sale at that rate is refused.
- **Payment type** is never empty: the company default, else credit for an
  invoice and cash for a sales receipt. POS methods are guessed from what
  they are (cash, customer account, M-Pesa, card, cheque) and can be changed.
- **Not registered for VAT** (Settings): eTIMS still applies. Lines go to
  KRA as Non-VAT (type D), and a line carrying VAT is flagged.
- **KRA item class per product category**, taken by products without one
  (a class on "All" covers the down payment and discount products too). The
  item class is also a column of the product list, for editing many at once.
- **Warnings on drafts**: before posting, an invoice or credit note says
  what eTIMS would refuse (product setup, VAT, PIN, deductions, the invoice a
  credit note corrects, its KRA reason), and warns about cash rounding that
  changes the VAT amount. A credit note made by hand names its invoice under
  *Credits Invoice* on the eTIMS tab.

## Reverse invoices (DigiTax)

Tick **eTIMS Reverse Invoice** on a vendor bill when the seller cannot issue
an eTIMS invoice, a small-scale farmer for instance. On posting, the bill is
queued and signed through DigiTax's `/reverse-invoices`. A line with no VAT
is sent as tax type D (Non-VAT). Once the bill is signed, its CU invoice
number becomes the bill's *Supplier eTIMS Invoice No.* The tick is offered
only when the company's device can issue reverse invoices.

## Point of Sale

A POS order is signed when it is paid. The receipt waits for the signature
for up to `ke_etims_sign_timeout` seconds (5 by default) and otherwise prints
"eTIMS: pending". The order stays queued, and a reprint shows the signature
once there is one. Refunds go to KRA as credit notes, with the shop's default
reason code. An order that is invoiced is signed once, as its invoice.

## Inventory and purchases

- **Stock movements.** Done moves are reported as KRA movement types
  02/03/04/05/06 in and 11/12/13/14/15/16 out. A picking with a single type
  is one movement. The stock master (quantity on hand) follows each accepted
  movement. A delivery for a sale waits until that sale is signed.
- **Supplier invoices** that KRA holds under your PIN are fetched daily
  (Kenya Tax > eTIMS Supplier Invoices) and turned into draft bills.
  Supplier items are linked to your products once, and the link is
  remembered. Posting the bill confirms the purchase to KRA.
- **Customs imports** are fetched daily, linked to a product, and approved
  or rejected (Kenya Tax > eTIMS Imports).

## VAT bridge

- **iTax matching on eTIMS numbers.** A row in an iTax auto-populated CSV
  that the VAT engine cannot place by invoice number or bill reference is
  tried against the eTIMS numbers: the CU invoice number (SCU ID / receipt)
  and the trader invoice number on sales, and the supplier's CU invoice number
  on purchases. Only a number that names exactly one document places a row,
  and the amounts are compared as usual.
- **eTIMS numbers beside the figures,** on the reconciliation and on the
  journal items each VAT box drills down to.
- **Bills without eTIMS** (Kenya Tax > iTax Reconciliation). These are a
  local supplier's posted bills with no supplier eTIMS invoice number, dated
  from *Expenses Need eTIMS Invoices From* (2026-01-01 by default; set in
  Settings). Without such an invoice the expense is not deductible, and the
  bill shows a warning while it can still be fixed. Mark a supplier (a bank,
  an airline, employees) or a single bill as *No eTIMS Invoice Needed* to
  leave it out.

## Security

- The **eTIMS Manager** group implies Accounting Administrator and manages
  devices, credentials, logs and number releases. Invoicing users and
  accountants can read the queue. They send through the invoice, not by
  editing documents.
- Device secrets (CMC key, API key, callback token) can be read only by
  eTIMS managers.
- Logs store requests and answers with `cmcKey`, API keys and tokens
  redacted, and error messages never contain them.
- Every company-owned eTIMS record has a multi-company rule: devices,
  documents, logs, item registrations, branches, supplier invoices and
  imports. KRA's code lists are shared.

## Tests

```
odoo-bin -d test -i x_ke_etims_pos,x_ke_etims_mrp,x_ke_etims_vat,sale_stock \
  --test-tags /x_ke_etims,/x_ke_etims_pos,/x_ke_etims_stock,/x_ke_etims_mrp,/x_ke_etims_vat \
  --stop-after-init
```

- KRA OSCU is tested against `etims.testing.FakeKra`, a strict in-process KRA
  stand-in, with dropped requests and lost answers. Both direct backends run
  the same tests.
- DigiTax is tested against a stand-in that patches `requests`.
- Every test database also holds a US company, which the module must leave
  alone.
- The POS receipt is tested with a browser tour, which needs Chromium and
  `websocket-client`.

## Not verified, not done

- **DigiTax field names.** The endpoints, base URL and header are DigiTax's,
  but its API reference could not be read from the build environment. The
  JSON field names in `backends/digitax.py` are tolerant guesses and must be
  checked against the reference before going live. This applies especially
  to reverse invoices (`/reverse-invoices`, and `/reverse-invoices/{id}` for
  status) and the supplier CU invoice number on purchases.
- **KRA `saveItemComposition`** follows KRA's naming. It is not in the
  `etims` library or FakeKra, so it has not been tested against them.
- **`spplrSdcId`** (the supplier's SCU ID in KRA's purchase list) is read if
  present. Without it, a bill made from an eTIMS purchase carries KRA's
  reference for the purchase instead of the supplier's CU number.
- **TIS requirements not covered:** X and Z daily reports, the PLU report,
  the stock-availability check before a sale (TIS 6.29), the credit-note log
  book and the TIS version display.
- **Out of scope:** VSCU, credit notes on reverse invoices, and TaxID (not
  KRA-approved).
