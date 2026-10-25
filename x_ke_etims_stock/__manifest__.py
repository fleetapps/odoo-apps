# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya - eTIMS Inventory and Purchases",
    "countries": ["ke"],
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Stock movements and stock levels to eTIMS, supplier invoices "
               "fetched from eTIMS as draft bills and confirmed, and customs "
               "imports linked to products and approved.",
    "description": """
Kenya - eTIMS Inventory and Purchases
=====================================

* **Stock movements.** A done receipt, delivery, adjustment, scrap or
  transfer between branches is reported to eTIMS with KRA's movement type,
  then the stock master with the quantity on hand. A delivery for a sale waits
  until that sale's invoice is signed, because KRA refuses a stock movement
  for a sale it has not seen.
* **Purchases.** Every day the sales your suppliers declared with your PIN
  are fetched, matched to the supplier by PIN and turned into draft vendor
  bills; a mapping wizard links their items to your products. Posting the
  bill confirms the purchase to eTIMS.
* **Imports.** Imports customs recorded under your PIN are fetched daily,
  linked to a product and approved or rejected.

See x_ke_etims/README.md.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "LGPL-3",
    "depends": ["x_ke_etims", "stock", "purchase"],
    "data": [
        "security/ir.model.access.csv",
        "security/x_ke_etims_stock_security.xml",
        "data/ir_cron_data.xml",
        "views/ke_etims_purchase_views.xml",
        "views/ke_etims_import_views.xml",
        "views/ke_etims_device_views.xml",
        "wizard/ke_etims_purchase_map_views.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
