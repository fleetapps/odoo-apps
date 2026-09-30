Kenya VAT Engine & iTax Ledger
==============================

Renders the VAT3 and withholding-VAT returns that ``l10n_ke`` already ships,
drills from any box to the journal items behind it, locks the period on close,
and reconciles the result against KRA's auto-populated iTax CSVs.

Installation
------------

Install ``Kenya VAT Engine & iTax Ledger`` from Apps. It depends on ``account``
and ``l10n_ke``; both are Community modules and no Enterprise subscription is
required.

On install the module repoints each Kenyan company's ``ST0EXPORT`` tax from the
exempt tag to the zero-rated tag, and does so again whenever the Kenyan chart of
accounts is loaded or reloaded (an upgrade of ``l10n_ke`` reloads it). Invoices
posted before the correction keep the tag they were stamped with -- see
*Repairing historical export tags* below.

Configuration
-------------

Under **Settings -> Invoicing -> Kenya VAT**:

* **Post the VAT entry on close** is off by default. Leave it off while you run
  the module in parallel against a period you have already filed, so nothing is
  written to the ledger. Turn it on, and set the accounts it reveals, once you
  are ready for closing to post the period's VAT movement: output VAT (box 6)
  and input VAT (box 12) are cleared from their control accounts, the
  non-deductible share of input VAT (boxes 14 and 16) is expensed to the
  *Non-deductible VAT Account*, VAT on imported services (box 13) is credited
  from the *Import VAT Account* (the input VAT account unless you say
  otherwise), and the balance -- box 18 -- lands on the payable or credit
  account. One entry per return: reopening sets it back to draft and closing
  again rewrites and reposts it.
* **Tax return lock date** is set automatically when a return is closed and is
  never lowered automatically. This is where you lift a lock you have decided to
  lift.

Two groups are installed.

*Kenya VAT: User* can prepare returns, enter the manual boxes, reconcile against
iTax and open any box through to the journal items behind it. It carries
*Show Accounting Features - Readonly*, because reading journal items is inherent
to the role.

*Kenya VAT: Manager* can additionally close a period, reopen one, and run the
export tag repair. It carries *Show Full Accounting Features*, because closing
can post the period's VAT entry and reopening sets that entry back to draft --
neither is possible with read-only accounting access.

System administrators get the manager group automatically.

Usage
-----

**Preparing a return.** Kenya VAT -> VAT Returns -> New. The period defaults to
last month. Press *Compute* and the 26 boxes are filled from your posted journal
items. The magnifier on any row opens the journal items behind that box in
Odoo's tax audit view -- including on the aggregated boxes, which open
everything they are built from.

**The six manual boxes.** Input VAT attributable to exempt supplies, input VAT
on mixed supplies, refund claims lodged, VAT paid, and the credit and debit
adjustments are not derivable from the ledger. Enter them on the *Manual
Entries* page and recompute. They are stored per period.

**Closing.** *Close Period* freezes the figures, carries any credit forward so
next month's box 19 picks it up, and sets the tax lock date. From then on an
entry dated inside the period and posted later is not refused: Odoo moves its
accounting date to the first open day after the lock, so it reports in the next
return. Changing the tax figures of an entry already inside the period is
refused. Periods close in order -- once one period has been closed, the period
immediately before a return must be closed before that return can be, because
box 19 reads the credit carried forward from the most recently closed period.
Closing the same period twice never carries the credit twice. Reopening reverts
the state but deliberately leaves the lock in place; a closed return cannot be
deleted without reopening it first.

**Reconciling against iTax.** Download the auto-populated CSVs from iTax, one
per section, and import each against the return for that period. Every row is
classified: matched, in KRA but not in Odoo, in Odoo but not in KRA, amount
variance, or PIN variance. KRA loads these files in batches through the month,
so re-import the fuller file later -- importing replaces the rows rather than
adding to them.

The *Lumpsum* figure on each sales import is the total of sales you have posted
that KRA has not received. The law still requires those to be declared, and that
is the number to enter in the return's lumpsum field.

Input tax is tracked against its six-month deadline. Suppliers flagged as being
on the KRA VAT special table are excluded from the claim automatically, because
a return carrying one is rejected at filing; their credit notes are not
excluded, matching KRA's own relaxation.

**Repairing historical export tags.** Kenya Tax -> Configuration -> Repair Tax
Tags (managers only) shows whether the tax definition currently carries the
correction and can re-apply it, then reports every posted journal item where an
export was tagged exempt, broken down by period, and retags them on your
instruction. Periods already closed to tax changes are reported as skipped
rather than silently rewritten.

Known limitations
-----------------

* Implements the three computation engines Kenya's returns use -- tax tags,
  aggregation and external values. A report needing account-code prefixes, Odoo
  domains or custom Python is refused by name rather than rendered wrongly.
* Implements two of the six date scopes, which are the two Kenya uses.
* No comparison columns, foldable hierarchies, analytic filters or PDF output.
* The VAT entry posted on close settles the period's own VAT movement (boxes
  6, 12, 13, 14, 16 and 18). Boxes 19 to 21 are settlement positions against
  accounts this module does not own, and belong with a withholding-VAT
  certificate register.
* Mixed-use input VAT (box 16) is apportioned strictly by the share of taxable
  turnover. The former 90/10 de minimis thresholds were abolished by the Tax
  Laws (Amendment) Act, 2024 with effect from 27 December 2024.
* ``l10n_ke.item.code.tax_rate`` uses the letters C, E and B with meanings that
  differ from KRA's own tax-type codes, where A is exempt, B is 16%, C is 0%,
  D is non-VAT and E is 8%. Do not map that field onto the OSCU ``taxTyCd`` in
  an eTIMS integration: exempt supplies would transmit as 8% and 8% petroleum as
  16%.

Support
-------

developers@fleet.ke
