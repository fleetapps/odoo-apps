# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "M-Pesa Online Payments (STK Push)",
    "summary": "Pay now with M-Pesa on the customer portal and the website: a "
    "prompt on the customer's phone, booked and reconciled by receipt number",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["x_ke_mpesa", "payment", "account_payment"],
    "data": [
        "views/payment_mpesa_templates.xml",
        "views/payment_provider_views.xml",
        "views/mpesa_transaction_views.xml",
        # Depends on the inline form template.
        "data/payment_provider_data.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "x_ke_mpesa_payment/static/src/interactions/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
}
