# M-Pesa statements: `x_ke_mpesa_statement`

Two ways to find the receipts no C2B confirmation brought, and the money that
leaves a till without a receipt (charges, sweeps to the bank, reversals).
Built on [`x_ke_mpesa`](../x_ke_mpesa/README.md). Whatever the source, a receipt
gets exactly one statement line (`MPESA-<receipt>`) in its till's daily
statement, so neither source can book a receipt twice.

| Source | Reaches | When |
|---|---|---|
| **Pull API** | the last 48 hours | every hour (the last two hours), and 48 hours back when set up |
| **Org Portal statement** (CSV export) | any period | by hand, e.g. weekly or at month end |

## Pull API

On the shortcode, **Statements** tab:

1. Enter the **Nominated number** (the business's Safaricom number nominated
   for the Pull API, as agreed with Safaricom) and, if the registration is
   held by another number (say the store), the **Pull API shortcode**.
2. **Register Pull API**. In production this is one-time.
3. **Pull every hour** creates the till journal's online statement provider
   (OCA `account_statement_import_online`, service *M-PESA Pull API*) and
   fetches the last 48 hours at once, in the background.

Each hourly run asks for the last two hours: since the previous run, plus an
hour of overlap for receipts Safaricom records late. After an outage it
reaches back to the last successful run, up to 48 hours. Asking the pull
wizard for anything older than that posts a note on the provider: import the
Org Portal statement for those days.

Rows go through the receipt log, like a C2B confirmation:

* a receipt Odoo already holds changes nothing. A different amount or till is
  flagged on the receipt, as for any other source.
* a receipt Odoo lacks is added (source *Pull API*), gets its statement line
  in the day it was paid, and is matched like any other (STK origin, POS
  claim, paybill account number).
* rows that are not a customer paying (B2C, reversals, charges, amounts of
  zero or less) are left out.
* the customer's number is kept masked, never in full.

The provider's chatter and the shortcode's *Last pull* fields show what each
pull found. The provider's pull wizard works as for any OCA provider. Its
*Debug* button shows the rows without booking anything.

**Times.** The Pull API writes `2026-10-25T09:48:33Z`. Whether that `Z` means
UTC is for Safaricom to confirm. Check one receipt against the customer's SMS
and set *Pull API times are in* (Settings › Invoicing › M-Pesa) to match.

A Pull callback (`/pay/ke/<token>/pull`), if Safaricom posts rows there, is
booked the same way.

## Org Portal statement

Export the statement of the till's account from the M-PESA Org Portal (CSV)
and use **Import M-PESA statement** on the shortcode. Importing takes *Show
Full Accounting Features*, as any statement import does (accountants have it
under Odoo Mates' accounting). The journal's *Import
statement* button works too: the *M-PESA Org Portal statement* mapping is the
default on every M-Pesa journal.

The mapping (Invoicing › Configuration › Accounting › Statement Sheet Mappings) reads the
standard export columns: *Receipt No.*, *Completion Time*, *Details*,
*Transaction Status*, *Paid In*, *Withdrawn*, *Balance*, *Reason Type*,
*Other Party Info*, *Linked Transaction ID*, *A/C No.* It finds the column
header below the account details the portal puts on top. **Check it against
a real export of each account before go-live** and edit the mapping if
Safaricom's column names or time format differ.

Each *Completed* row is booked into its day's statement:

| Row | Becomes |
|---|---|
| Money in | a receipt, through the receipt log (as for the Pull API), `MPESA-<receipt>` |
| A charge | a line `MPESA-<receipt>-CHG`, booked to the **M-Pesa charges** account |
| A sweep to the bank | a line `MPESA-<receipt>-SWP`, booked to the **internal transfer account**; the bank statement's matching line meets it there |
| A reversal | a line `MPESA-<receipt>-REV`, left for the accountant; the receipt it reverses is flagged *Reversed by M-PESA* |
| Anything else going out | a line `MPESA-<receipt>-OUT`, left for the accountant |

Declined, expired or cancelled rows and the totals at the foot are skipped.
Charges and sweeps are told apart by words in the reason type or details:
*Charge rows contain* and *Sweep rows contain* in Settings › Invoicing › M-Pesa, which
also set the charges and sweeps accounts (the charges account is created on
first use). Full customer numbers in the details are masked before they are
stored. Importing the same file twice adds nothing.

**Balance check.** The *Balance* after each day's last row is recorded on that
day's statement, next to what Odoo holds for the till's journal (for a store:
with its tills' journals) at the end of the same day. *Reporting › M-Pesa ›
M-PESA Balance Check* lists them, in red where they differ. A constant
difference is an opening balance not yet booked. A difference that changes
from one day to the next is a row Odoo does not have.

## Reports

* **Settlements and Charges** (*Reporting › M-Pesa*, report 8): per till per
  month, what it received, paid in charges, swept to the bank, had reversed,
  and the lines still to look at. List, pivot and graph.
* **M-PESA Balance Check**: the daily balances above.
* On bank statement lines: filters *M-Pesa charges*, *M-Pesa sweeps* and
  *M-Pesa rows to look at*.

## Daily statements stay valid

Odoo works out a statement's opening balance once, when the statement is
created. A receipt backfilled into an earlier day would leave every later day
"invalid". `x_ke_mpesa` therefore moves the later days' opening balances
whenever a line lands in an earlier day.

## For Safaricom to confirm

These are settings or editable data, not guesses in the code (PLAN §9.1):

* the Pull API: whether `trxDate` is UTC, the page size and meaning of
  `OffSetValue` (read here as rows to skip, with a guard against an offset
  that is ignored), which number is nominated, and whether the registration is
  per till or per store;
* the Org Portal export's columns, time format and reason types;
* whether a till's charges come one per receipt or once a day (both work).

## Tests

```
odoo -d test -i x_ke_mpesa_statement --test-tags /x_ke_mpesa_statement --stop-after-init
```

Against the fake Daraja: registration (nominated number, store number,
already registered), set-up and the 48-hour backfill, the hourly window and
the one after an outage, the 48-hour limit, pages and an ignored offset, the
time zone setting, rows that are not receipts, a paybill account number, the
debug view, the Pull callback, and a bad row among good ones. Org Portal: a
full statement (known and new receipts, charge, sweep, reversal, declined and
total rows, daily balances), a second import, a balance gap, a store's
statement listing its tills' receipts, a paybill account number, the Pull
schedule surviving an import, report 8, and a plain bank journal importing as
before.

## Licence

LGPL-3. Depends on OCA `account_statement_import_online` and
`account_statement_import_sheet_file` (AGPL-3).
