# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Interactive Profit & Loss",
    "summary": "A one-page, drill-down Profit & Loss for Odoo Community: "
    "comparisons, budgets, what's in every number, entry peek, saved and emailed views",
    "description": """
The Profit & Loss of Odoo 19 Enterprise, rebuilt for Community on one page:
unfold a line into its accounts and journal items in place, split any figure
by partner, product, month, journal or analytic account, peek at the source
document beside the report, compare periods, type a budget straight into the
report, and save views that are emailed on a schedule. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["account", "mail"],
    "data": [
        "security/odin_pnl_security.xml",
        "security/ir.model.access.csv",
        "data/odin_pnl_layout_data.xml",
        "views/odin_pnl_layout_views.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_account_pnl/static/src/pnl/**/*",
        ],
        "web.assets_tests": [
            "odin_account_pnl/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
