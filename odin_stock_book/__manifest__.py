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
posting creates the manufacturing order, delivery, return, purchase order with
its receipt, or scrap.
See README.md.
""",
    "version": "19.0.1.8.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["stock_account", "purchase_stock", "sale_stock", "mrp", "mail"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/stock_book_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/stock_book_views.xml",
        "views/menus.xml",
        "report/movement_log.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_stock_book/static/src/book/**/*",
        ],
        "web.report_assets_common": [
            "odin_stock_book/static/src/report/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
