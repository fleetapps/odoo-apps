{
    "name": "Motor Quotes",
    "summary": "Quote motor cover on one screen: the vehicle, every insurer's "
    "rate for it with PVT, excess protector, PLL and courtesy car, side by "
    "side; saved to CRM, printed, accepted and invoiced like medical quotes",
    "description": """
Motor Quotes
============

New quote ▸ Motor: the client, the vehicle (type, comprehensive or third
party only, value, year, passengers or tonnage where they count), then every
insurer's price for it -- the basic premium from their rate (a % of the
value with a minimum, or a flat third-party premium), the extras each
offers, levies and stamp duty -- compared and saved as one quote.

Rates live in Cover Quotes ▸ Configuration ▸ Motor rates: one line per
insurer, vehicle type, cover and band. See README.md.
""",
    "version": "19.0.1.1.0",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_medical_quote"],
    "data": [
        "security/ir.model.access.csv",
        "data/motor_class_data.xml",
        "data/product_data.xml",
        "data/mail_template_data.xml",
        "views/motor_rate_views.xml",
        "views/cover_product_views.xml",
        "data/cover_product_data.xml",
        "views/motor_quote_actions.xml",
        "views/quote_comparison_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_motor_quote/static/src/**/*",
        ],
        "web.assets_tests": [
            "odin_motor_quote/static/tests/tours/**/*",
        ],
    },
    "application": False,
    "installable": True,
}
