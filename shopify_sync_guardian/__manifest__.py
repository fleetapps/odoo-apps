# Manifest reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Shopify Sync Guardian",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "summary": "Sync-health monitoring for your Shopify connector: failed-order "
               "queue, auto-retry, orphan & duplicate reconciliation, alerts.",
    "author": "YourCompany",
    "website": "https://yourcompany.example",
    "license": "OPL-1",
    "price": 249.00,
    "currency": "USD",
    # Deliberately depends only on core: connectors are detected at runtime so
    # ONE app serves Emipro / VentorTech / TeqStars installs.
    "depends": ["sale_management", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/guardian_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": True,
}
