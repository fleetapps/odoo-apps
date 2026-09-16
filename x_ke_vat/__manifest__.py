# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya VAT Engine & iTax Ledger",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Render Kenya's VAT3 and withholding-VAT returns on Odoo "
               "Community, drill from any box to the journal items behind it, "
               "lock the period, and reconcile line by line against KRA's "
               "auto-populated iTax CSVs.",
    "description": """
Kenya VAT Engine & iTax Ledger
==============================

Community already knows Kenyan tax. It just has no screen to show it on.

``l10n_ke`` ships the Kenyan chart of accounts, the taxes, the KRA item codes,
a 26-box VAT3 return definition and a 2% withholding-VAT return definition --
all of it LGPL-3, all of it in Community. Every invoice and bill you post is
already stamped, box by box, with the right KRA tax tags.

What Community does not ship is the layer that adds them up. That lives in
``account_reports``, which is Enterprise. This module is that layer, and
nothing more than that layer.

A renderer, not a tax engine
----------------------------
The VAT3 definition stays Odoo's. We walk the ``account.report`` records that
``l10n_ke`` already installs and resolve the three computation engines Kenya
actually uses -- tax tags, aggregation and external values -- against your
journal items. You inherit KRA's box numbering, and you inherit any correction
Odoo publishes upstream.

* **Every box drills down.** Click box 17 and you get the journal items behind
  it, in Odoo's own tax audit view. This is the feature that makes a return
  reconcilable rather than merely printable.
* **The six manual boxes are editable** and persist per period, and the credit
  carried forward in box 26 is written back so box 19 picks it up next month --
  once, however many times you close.
* **Closing locks the period.** The box values freeze and the tax lock date is
  set, so the numbers cannot drift after filing.
* **Periods are the ones your instance already knows.** Where statutory periods
  have been seeded, a return is named and scoped by them rather than by dates
  this module invents, so a return and a report filter mean the same month.

Reconciles against iTax, because that is the real job
-----------------------------------------------------
Since March 2024 KRA pre-fills the VAT return from eTIMS, TIMS and customs, and
the pre-filled lines cannot be edited. So the question that matters on the 19th
of the month is not "what are our numbers" but "why do ours differ from KRA's".

Import the auto-populated CSVs and every row is matched against your ledger and
classified: matched, in KRA but not in Odoo, in Odoo but not in KRA, amount
variance, PIN variance. The untransmitted sales total you owe KRA as a lumpsum
declaration falls out of the same pass, and input tax you have chosen to defer
is tracked against its six-month deadline instead of quietly lapsing.

Suppliers KRA has put on the VAT special table are excluded from the claim
automatically, because a return carrying one is rejected at filing -- and their
credit notes are not excluded, matching KRA's own relaxation.

Built on Kenya Tax Base
-----------------------
The supply classification and the corrections to ``l10n_ke`` live in
``x_ke_base``, which installs with this module. They are there rather than here
because the eTIMS transmitter has to classify a supply exactly the way this
return does, and derive it twice and the two will disagree -- on figures KRA
does not let you edit afterwards.
    """,
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "OPL-1",  # Odoo Proprietary License (paid App Store module)
    "price": 299.00,
    "currency": "USD",
    # x_ke_base carries account and l10n_ke. Nothing AGPL-3 may be added here:
    # date_range, report_xlsx, queue_job, account_financial_report and
    # mis_builder are all off-limits to an OPL-1 module. date.range is read
    # softly at runtime through x_ke_base, where it is present.
    "depends": ["x_ke_base"],
    "data": [
        "security/x_ke_vat_security.xml",
        "security/ir.model.access.csv",
        "views/ke_vat_return_views.xml",
        "views/ke_vat_itax_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        # Menus last: they reference the actions defined above.
        "views/x_ke_vat_menus.xml",
    ],
    "installable": True,
    "application": True,
}
