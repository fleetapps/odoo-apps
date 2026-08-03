# Manifest reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "MCP Governance Suite",
    "version": "19.0.1.1.0",
    "category": "Extra Tools/AI",
    "summary": "Model Context Protocol server for Odoo with the governance layer: "
               "per-user tool scoping, full audit log, read-only mode, approval "
               "gates for writes, token cost tracking.",
    "description": """
MCP Governance Suite
====================

Serve an MCP (Model Context Protocol) endpoint from Odoo - and keep control of
what the agent on the other end is allowed to do.

* **Scopes** - an allow-list of models and operations. Anything not listed is
  denied. Per-model field blacklists and record domains narrow it further.
* **API keys** - each bound to one Odoo user and one scope. Calls execute as
  that user, so ``ir.model.access`` and record rules apply on top of the scope;
  a scope can only ever narrow what the user could already do.
* **Approval gates** - writes from a scope marked *Require Approval* pause as a
  request for a human, instead of the agent proceeding on its own.
* **Audit log** - one row per tool call: which key, which tool, allowed or
  refused, how long it took and a size-based token estimate for cost tracking.
* **Limits** - default and maximum record counts per search, and a per-key
  calls-per-hour ceiling.

The dashboard is the landing page: endpoint status, activity for the last two
weeks, the busiest tools and keys, what is waiting on an approver, and a setup
checklist that puts the three steps in the order that actually works.
""",
    "author": "YourCompany",
    "website": "https://yourcompany.example",
    "license": "OPL-1",
    "price": 299.00,
    "currency": "USD",
    "depends": ["base", "web", "mail"],
    "data": [
        "security/mcp_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        # Before mcp_views.xml: the Configuration menu there points at the
        # settings action defined here.
        "views/res_config_settings_views.xml",
        "views/mcp_views.xml",
    ],
    "assets": {
        # https://www.odoo.com/documentation/19.0/developer/reference/frontend/assets.html
        "web.assets_backend": [
            "mcp_governance_suite/static/src/scss/mcp_dashboard.scss",
            "mcp_governance_suite/static/src/js/mcp_dashboard.js",
            "mcp_governance_suite/static/src/xml/mcp_dashboard.xml",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
}
