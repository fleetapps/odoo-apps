# Manifest: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "AI Bill OCR for Community",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Vendor-bill AI capture for Odoo Community: email-alias intake, "
               "LLM extraction with your own API key (OpenAI / Anthropic / "
               "Gemini), confidence-scored review queue, one-click draft bill.",
    "author": "YourCompany",
    "license": "OPL-1",
    "price": 179.00,
    "currency": "USD",
    # Community-only dependencies on purpose: this is the Enterprise-parity play.
    "depends": ["account", "mail"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/mail_alias.xml",
        "data/ir_cron.xml",
        "views/inbox_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": True,
}
