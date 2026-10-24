# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya - eTIMS Point of Sale",
    "countries": ["ke"],
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Sign Kenyan POS orders with eTIMS and print the KRA signature "
               "and QR code on 80 mm and 58 mm receipts, with an offline fallback.",
    "description": """
Kenya - eTIMS Point of Sale
===========================

Every paid POS order of a Kenyan company that uses eTIMS is signed. When an
order is validated the till asks the server to sign it straight away, with a
short timeout; the receipt then carries the SCU information and QR code. If
the till is offline or eTIMS is slow, the receipt says "eTIMS: pending" with
the order reference, the order waits in the eTIMS queue, and a reprint from
the order list carries the signature once it is signed. Reprints say COPY.

Refunds are credit notes against the order they refund. An order that is
invoiced is signed once, as its invoice. See x_ke_etims/README.md.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "LGPL-3",
    "depends": ["x_ke_etims", "point_of_sale"],
    "data": [
        "views/pos_config_views.xml",
        "views/pos_payment_method_views.xml",
        "views/pos_order_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "x_ke_etims_pos/static/src/**/*",
        ],
        "web.assets_tests": [
            "x_ke_etims_pos/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "auto_install": True,
    "application": False,
}
