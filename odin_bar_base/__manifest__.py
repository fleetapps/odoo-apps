{
    "name": "Bar Base",
    "summary": "One definition of a bar: location, operation types, analytic "
    "account, POS mapping and trading days",
    "description": """
One definition of a bar, shared by the Bar Desk and the POS importer: its
stock location, operation types, analytic account, POS mapping and trading
day. A POS importer records here which days' sales are in stock.
See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["stock", "analytic"],
    "data": [
        "security/odin_bar_security.xml",
        "security/ir.model.access.csv",
        "views/odin_bar_views.xml",
        "views/odin_bar_pos_day_views.xml",
        "views/stock_picking_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
}
