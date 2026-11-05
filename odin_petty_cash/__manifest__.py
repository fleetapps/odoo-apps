# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Petty Cash",
    "summary": "A petty cash book that looks like the spreadsheet, fed by the "
    "M-Pesa statement: matching rules pick the account, approvals post one "
    "entry per voucher, stock bought with petty cash goes into stock",
    "description": """
The petty cash book your team already knows - date, voucher, description, paid
to, phone, method, M-Pesa code, paid, charges, total, balance, approved by -
on one page, by month, with the M-Pesa statement filling most of it in.
See README.md.
""",
    "version": "19.0.1.1.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["account", "stock_account", "mail"],
    "external_dependencies": {"python": ["openpyxl", "xlsxwriter"]},
    "data": [
        "security/petty_cash_security.xml",
        "security/ir.model.access.csv",
        "views/petty_cash_line_views.xml",
        "views/petty_cash_float_views.xml",
        "wizard/petty_cash_import_views.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_petty_cash/static/src/book/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
