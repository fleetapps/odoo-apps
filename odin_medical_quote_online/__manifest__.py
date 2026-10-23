{
    "name": "Medical Quotes Online",
    "summary": "A public page where anyone can price medical cover for their "
    "family in plain words, keep their quote and ask to be called; the "
    "quote lands in CRM with a to-do for the agent. Shareable on WhatsApp",
    "description": """
Medical Quotes Online
=====================

A self-service version of Add Medical Quote for people who know nothing
about insurance: who needs cover and their ages, then the plans that fit
with their price, then where to send the quote. Their quote has its own
private page with the PDF, *Talk to an advisor* and *Chat with us on
WhatsApp*.

The office sees each one as an opportunity tagged *Online quote*, assigned
to the agent whose link was used (or the online-quotes user), with a to-do
due today and the quote in the Quotes register. Agents share the page on
WhatsApp from any lead -- prefilled for that client -- or as their own link.

No Website app needed. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_medical_quote", "portal"],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_activity_data.xml",
        "views/templates.xml",
        "views/res_config_settings_views.xml",
        "views/sale_order_template_views.xml",
        "views/quote_comparison_views.xml",
        "wizard/share_link_views.xml",
        "views/crm_lead_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "odin_medical_quote_online/static/src/online_quote/**/*",
        ],
        "web.assets_tests": [
            "odin_medical_quote_online/static/tests/tours/**/*",
        ],
    },
    "application": False,
    "installable": True,
}
