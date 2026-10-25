# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya - eTIMS VAT Bridge",
    "countries": ["ke"],
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "eTIMS numbers in the VAT return and the iTax reconciliation; "
               "supplier bills with no eTIMS invoice, before they cost a deduction.",
    "description": """
Kenya - eTIMS VAT Bridge
========================

Installs by itself when both Kenya - eTIMS and the Kenya VAT Engine are.

* **iTax rows match on eTIMS numbers.** KRA pre-fills the return from eTIMS,
  so its rows carry eTIMS's numbers: the CU invoice number (SCU ID / receipt)
  or the trader invoice number sent with the sale, and on purchases the
  supplier's CU invoice number. A row the VAT engine cannot place by the Odoo
  invoice number or the bill reference is tried against those before it is
  reported as "in KRA, not in Odoo".
* **eTIMS numbers beside the figures.** On the reconciliation and on the
  journal items each VAT box drills down to.
* **Bills without an eTIMS invoice.** From the 2026 year of income an expense
  a local supplier did not back with an eTIMS invoice is not deductible. Such
  bills are listed under Kenya Tax, and the bill says so while it can still be
  fixed. Suppliers and bills the rule does not apply to (imports, a bank's
  charges, wages) can be marked as such.

See x_ke_etims/README.md.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "LGPL-3",
    "depends": ["x_ke_etims", "x_ke_vat"],
    "data": [
        "views/account_move_views.xml",
        "views/ke_vat_itax_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
