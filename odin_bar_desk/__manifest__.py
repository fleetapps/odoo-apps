{
    "name": "Bar Desk",
    "summary": "Phone-first stock control for bars: log moves, count every "
    "location against the expected stock, explain differences, approve the day",
    "description": """
Phone-first stock desk for a club's bars and store, run by one stock
controller: log the day's moves between locations, count every location the
next morning against the stock expected (last count + moves - POS sales),
give each difference a reason, and approve the day. Supplier deliveries are
received at the store against the invoice. See README.md.
""",
    "version": "19.0.1.10.0",
    "category": "Inventory/Inventory",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_bar_base", "hr", "purchase_stock"],
    "data": [
        "security/odin_bar_desk_security.xml",
        "security/ir.model.access.csv",
        "views/bar_desk_actions.xml",
        "views/odin_bar_count_views.xml",
        "views/odin_bar_activity_views.xml",
        "views/odin_bar_reason_views.xml",
        "views/odin_bar_variance_reason_views.xml",
        "views/odin_bar_views.xml",
        "views/stock_picking_views.xml",
        "views/odin_bar_report_views.xml",
        "views/product_views.xml",
        "views/hr_employee_views.xml",
        "views/res_partner_views.xml",
        "views/res_users_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_bar_desk/static/src/bar_desk/**/*",
            "odin_bar_desk/static/src/bar_dashboard/**/*",
        ],
        "web.assets_tests": [
            "odin_bar_desk/static/tests/tours/**/*",
        ],
    },
    "post_init_hook": "_post_init_hook",
    "application": True,
    "installable": True,
}
