# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya Tax Base",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "One classification table behind Kenyan VAT and eTIMS, plus the "
               "corrections l10n_ke needs before either can be trusted. "
               "Date-ranged, so the October fuel rate change is already in it.",
    "description": """
Kenya Tax Base
==============

The shared layer under Kenyan VAT reporting and eTIMS transmission. On its own
it changes nothing you can see; it is what stops the two disagreeing.

One answer, two vocabularies
----------------------------
The VAT3 return and KRA's OSCU API ask the same question about every invoice
line -- what kind of supply is this -- and answer it differently. The return
wants a box number, one to twenty-six. The wire wants a tax type code, A through
E. Derived separately they drift, and the drift arrives as a variance on a
return KRA will not let you edit.

``ke.tax.classification`` holds both answers in one row, for all seventeen tax
tags the Kenyan returns reference, alongside the KRA item-code letters that
decide a product's rate in the first place.

Dated, because Kenyan rates move
--------------------------------
Kenya cut VAT on petrol, diesel and kerosene from 16% to 8% in April 2026 and
has extended the reduction twice, currently to 14 October 2026. Both windows
ship in the table, so a database built today already knows what happens on the
fifteenth -- the classification is resolved when it is asked, not when it is
installed.

Corrections to l10n_ke, and a tripwire
--------------------------------------
Odoo's Kenyan localisation carries four defects in the return. Each produces a
plausible figure rather than an error, which is why they survive:

* **Box 16** apportioned mixed-use input VAT by dividing VAT amounts by
  turnover. For a standard-rated trader that treats roughly 84% of the mixed
  input pool as non-deductible instead of none of it.
* **Export sales** were tagged exempt rather than zero-rated, so they reported
  in box 4 instead of box 3 -- and box 3 feeds the box 16 ratio while box 4 does
  not, so an exporter lost deductible input VAT twice over.
* **Box 26** expressed its carry-forward threshold in Romanian leu.
* **Box 20** read its base column from the purchase side of the ledger.

The report expressions are corrected by data. The tax tagging cannot be, because
Kenyan taxes are generated per company when the chart of accounts loads and have
no static external id -- so it is a method, ``ke_apply_tax_corrections``,
idempotent and safe to call before the chart exists. It runs on install and
again every time the Kenyan chart of accounts is loaded or reloaded, which is
the moment an upgrade of the localisation would otherwise put the defect back.

Every correction is re-checked whenever a return is computed, because all four
revert the same silent way if the localisation is upgraded afterwards.

Install this with **Kenya VAT Engine** rather than on its own.
    """,
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "OPL-1",  # Odoo Proprietary License (paid App Store module)
    "price": 0.00,
    "currency": "USD",
    # Only Community modules, and only the two that carry Kenyan tax content.
    # Nothing AGPL-3 may appear here: date_range, report_xlsx, queue_job,
    # account_financial_report and mis_builder are all off-limits to an OPL-1
    # module. date.range is read softly at runtime instead, where present.
    "depends": ["account", "l10n_ke"],
    "data": [
        "security/x_ke_base_security.xml",
        "security/ir.model.access.csv",
        # Classification before corrections: the corrections resolve tag names
        # through the table rather than naming them.
        "data/ke_tax_classification_data.xml",
        "data/ke_report_corrections.xml",
        "views/ke_tax_classification_views.xml",
        "wizard/ke_export_retag_wizard_views.xml",
        # Menus last: they reference the actions defined above.
        "views/x_ke_base_menus.xml",
    ],
    "post_init_hook": "post_init_apply_corrections",
    "installable": True,
    "application": False,
}
