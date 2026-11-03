# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Stock Book",
    "summary": "The store's movement log as a spreadsheet-like page: filling and "
    "production take their components from the recipe, every line posts the real "
    "Odoo moves, a stock sheet by month and a weekly count",
    "description": """
The MOVEMENT LOG and MAIN INVENTORY sheets the store keeps, inside Odoo: date,
item, added, removed, reason, customer, on hand after - typed in the last row.
Bottle filling and production batches fill their components from the recipe;
posting creates the manufacturing order, delivery, return, receipt or scrap.
See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["stock_account", "mrp", "mail"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/stock_book_security.xml",
        "security/ir.model.access.csv",
        "views/stock_book_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_stock_book/static/src/book/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
