# Manifest: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Shopify Connector - Two-Way Sync",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "summary": "Bidirectional Shopify sync: products, stock, prices, customers, "
               "orders, fulfillments & refunds. Webhook-driven inbound, queued "
               "outbound, field-level conflict policy.",
    "author": "YourCompany",
    "license": "OPL-1",
    "price": 399.00,
    "currency": "USD",
    "depends": ["sale_management", "stock", "delivery"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/connector_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/instance_views.xml",
        "views/binding_views.xml",
        "views/sync_job_views.xml",
    ],
    "installable": True,
    "application": True,
}
