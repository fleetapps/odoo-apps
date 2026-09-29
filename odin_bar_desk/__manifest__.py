{
    "name": "Bar Desk",
    "summary": "Phone-first bar stock desk: stock outs, blind counts with "
    "manager approval, delivery checks and a store mode",
    "description": """
Phone-first stock desk for bars: staff record stock outs, count the bar
blind at night and check deliveries on a shared tablet with a PIN; the store
sends stock to bars; managers approve counts in Bar Control. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_bar_base", "hr"],
    "data": [
        "security/odin_bar_desk_security.xml",
        "security/ir.model.access.csv",
        "views/bar_desk_actions.xml",
        "views/odin_bar_count_views.xml",
        "views/odin_bar_activity_views.xml",
        "views/odin_bar_reason_views.xml",
        "views/odin_bar_views.xml",
        "views/stock_picking_views.xml",
        "views/product_views.xml",
        "views/hr_employee_views.xml",
        "views/res_users_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_bar_desk/static/src/bar_desk/**/*",
        ],
        "web.assets_tests": [
            "odin_bar_desk/static/tests/tours/**/*",
        ],
    },
    "post_init_hook": "_post_init_hook",
    "application": True,
    "installable": True,
}
