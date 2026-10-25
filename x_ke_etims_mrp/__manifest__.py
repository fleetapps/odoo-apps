# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya - eTIMS Manufacturing",
    "countries": ["ke"],
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Item compositions from bills of materials to eTIMS; production "
               "consumption and output reported as stock movements.",
    "description": """
Kenya - eTIMS Manufacturing
===========================

* **Item composition.** A bill of materials tells KRA what a manufactured item
  is made of (saveItemComposition), one component at a time. Only KRA's own
  OSCU API has it; on other backends the button is not shown.
* **Production.** Components consumed and products made are stock movements
  KRA counts (processing, codes 14 and 05). Kenya - eTIMS Inventory reports
  them like any other done move; this module adds nothing to that but the test
  that it does.

See x_ke_etims/README.md.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "LGPL-3",
    "depends": ["x_ke_etims_stock", "mrp"],
    "data": [
        "views/mrp_bom_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
