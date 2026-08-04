# -*- coding: utf-8 -*-
# Module manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Shopify Connector - Two-Way Sync",
    "version": "19.0.1.4.0",
    "category": "Sales/Sales",
    "summary": "Bidirectional Shopify sync: products, stock, prices, customers, "
               "orders, fulfillments & refunds. Webhook-driven inbound, queued "
               "outbound, field-level conflict policy.",
    "description": "Real-time two-way Shopify connector built on the GraphQL "
                   "Admin API with a durable job queue, explicit per-entity "
                   "sync directions and an auditable conflict engine.",
    "author": "YourCompany",
    "website": "https://www.example.com",
    "support": "developers@fleet.ke",
    "license": "OPL-1",
    "price": 399.00,
    "currency": "USD",
    "depends": [
        "sale_management",
        "stock",
        "delivery",
        "account",
        "onboarding",
    ],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/connector_security.xml",
        "security/ir.model.access.csv",
        "data/product_data.xml",
        "data/onboarding_data.xml",
        "data/ir_cron.xml",
        "views/instance_views.xml",
        "views/oauth_templates.xml",
        "views/sync_job_views.xml",
        "views/binding_views.xml",
        "views/mismatch_views.xml",
        "views/conflict_views.xml",
        "views/backfill_views.xml",
        "views/preview_views.xml",
        "views/sale_order_views.xml",
        "views/product_views.xml",
        "views/payout_views.xml",
        "views/dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "shopify_bisync/static/src/**/*",
        ],
    },
    "images": ["static/description/banner.png"],
    "pre_init_hook": "pre_init_check",
    "uninstall_hook": "uninstall_hook",
    "post_load": "post_load",
    "installable": True,
    "application": True,
}
