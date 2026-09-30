# -*- coding: utf-8 -*-
# Module manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Odoo Shopify Sync",
    "version": "19.0.1.7.0",
    "category": "Sales/Sales",
    "summary": "Bidirectional Shopify sync: products, stock, prices, customers, "
               "orders, fulfillments & refunds. Webhook-driven inbound, queued "
               "outbound, field-level conflict policy.",
    "description": """
Odoo Shopify Sync
==================

Bidirectional Shopify sync built on the GraphQL Admin API — webhook-driven
inbound, queued outbound, and an explicit per-entity direction and conflict
policy instead of one the connector assumes for you.

* **Two-way products**, export prices, export/two-way inventory across
  multiple locations, import customers and orders, export fulfillments,
  import refunds as draft credit notes, and reconcile Shopify payouts line
  by line against the orders and invoices behind them.
* **You choose the conflict policy** — Odoo wins, Shopify wins, or newest
  edit wins — and every resolution is written to the Conflict Log, never
  silent.
* **Nothing is silently dropped.** An unmatched order line or a missing
  pricelist still lands, on a fallback and with a logged reason, in the
  Mismatch Log.
* **Self-healing webhooks and rate-limit awareness** — Shopify's own webhook
  subscriptions get silently dropped and re-registered daily; REST and
  GraphQL calls back off instead of failing under load.
* **Orders are real Odoo sale orders**, so Sales Analysis, pivots and every
  report you already use work on them without extra setup.
* **Multi-store and multi-company**, OAuth connect (no tokens to copy), and
  a resumable backfill wizard for existing catalogs.

No phone-home, no CDN — everything runs inside your Odoo.
""",
    "author": "Fleet",
    "website": "https://www.odin.ist",
    "support": "support@odin.ist,andrew@fleet.ke",
    "license": "OPL-1",
    "price": 349.00,
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
    "images": [
        "static/description/banner.png",
        "static/description/screenshot_what_syncs.png",
        "static/description/screenshot_stores_list.png",
        "static/description/screenshot_backfill.png",
        "static/description/screenshot_conflict_log.png",
        "static/description/screenshot_mismatch_log.png",
        "static/description/screenshot_payout.png",
        "static/description/screenshot_sales_analysis.png",
        "static/description/screenshot_sync_health.png",
        "static/description/screenshot_shopify_dashboard.png",
    ],
    "pre_init_hook": "pre_init_check",
    "uninstall_hook": "uninstall_hook",
    "post_load": "post_load",
    "installable": True,
    "application": True,
}
