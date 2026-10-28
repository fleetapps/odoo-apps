{
    "name": "Motor Quotes Online",
    "summary": "A public page where anyone can compare motor insurance for "
    "their vehicle in plain words, keep their quote and ask to be called; "
    "the quote lands in CRM like a medical online quote",
    "description": """
Motor Quotes Online
===================

The motor version of Medical Quotes Online: what you drive, full cover or
third party only, its value and year, then every insurer's price with the
extras each offers (riots and terrorism, excess protector, a replacement
car...), then where to send the quote. The client gets the same private
quote page, PDF and Talk to an advisor; the office gets the same
opportunity, to-do or team pool, and Quotes register entry.

Share it from Cover Quotes ▸ Share quote link (Motor), or from a lead
tagged Motor. See README.md.
""",
    "version": "19.0.1.1.0",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_motor_quote", "odin_medical_quote_online"],
    "data": [
        "data/motor_class_online.xml",
        "views/templates.xml",
        "views/motor_rate_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "odin_motor_quote_online/static/src/online_motor/**/*",
        ],
        "web.assets_tests": [
            "odin_motor_quote_online/static/tests/tours/**/*",
        ],
    },
    "application": False,
    "installable": True,
}
