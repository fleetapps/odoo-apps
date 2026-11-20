# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Stock Book: invoice photos",
    "summary": "Add sale from a phone photo of the signed invoice: Claude reads it, the items are matched "
    "here against the catalogue (kind, size, bulk or packaged), uncertain lines are flagged and nothing "
    "is saved until the storekeeper checks it",
    "description": """
Upload photo in the Stock Book's Add sale window: one photo per page of the
printed customer invoice. The window is filled in (customer and branch,
invoice no., date, terms, CU invoice no., and every line with its item,
quantity, rate and VAT) for the person to check and save. Lines the photo or
the catalogue leave in doubt are highlighted; rates are checked against the
customer's pricelist. The photos stay with the sale and on its invoice.

A bridge between Stock Book and Accounting AI, which installs itself when
both are installed: Stock Book does not need the AI, and the AI's consent,
key, budget and log are the ones in Accounting > Settings > Accounting AI.
See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["odin_stock_book", "odin_account_ai"],
    "data": [
        "security/ir.model.access.csv",
        "security/stock_photo_security.xml",
        "data/ir_cron_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_stock_book_ai/static/src/photo/**/*",
        ],
    },
    "installable": True,
    "auto_install": True,
    "application": False,
}
