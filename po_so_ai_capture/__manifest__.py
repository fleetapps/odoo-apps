# Manifest: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "PO to SO - AI Order Capture",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "summary": "Customer PO PDFs become draft sales orders: email-alias intake, "
               "LLM parsing with per-line confidence, fuzzy SKU matching that "
               "LEARNS each customer's part numbers, and a review queue built "
               "for the order-entry clerk.",
    "description": "Stop retyping customer purchase orders. Forward or alias "
                   "orders@ to Odoo; every PO PDF is parsed, matched and staged "
                   "as a draft SO for one-click human approval.",
    "author": "YourCompany",
    "license": "OPL-1",
    "price": 399.00,
    "currency": "USD",
    "depends": ["sale_management", "mail"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/capture_security.xml",
        "security/ir.model.access.csv",
        "data/mail_alias.xml",
        "data/ir_cron.xml",
        "views/capture_views.xml",
        "views/sku_alias_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": True,
}
