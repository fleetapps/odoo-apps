# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "M-Pesa at the Point of Sale",
    "summary": "An M-Pesa payment terminal for each till: prompt the customer's "
    "phone, pick a payment made to the till, or type a receipt that stays "
    "flagged until M-PESA confirms it; settled against the session at closing",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["x_ke_mpesa", "point_of_sale"],
    "data": [
        "security/x_ke_mpesa_pos_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/pos_payment_method_views.xml",
        "views/mpesa_shortcode_views.xml",
        "views/pos_session_views.xml",
        "report/mpesa_report_pos_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "x_ke_mpesa_pos/static/src/**/*",
        ],
    },
    "installable": True,
    "auto_install": False,
}
