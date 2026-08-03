# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Fleet AI — MCP Governance Suite",
    "version": "19.0.3.1.0",
    "category": "Extra Tools/AI",
    "summary": "Connect Claude, ChatGPT, Gemini, Cursor or any MCP client to Odoo "
               "with one-click OAuth 2.1 — every AI action runs as the real user, "
               "scoped, rate-limited, audited and optionally approval-gated.",
    "description": """
Fleet AI — MCP Governance Suite
===============================

Let your team *chat with their Odoo data* from Claude, ChatGPT, Gemini, Cursor,
Copilot, VS Code — any Model Context Protocol client — and get reports,
dashboards and answers in seconds. The MCP server runs **inside Odoo**: no extra
process, no middleware, and **zero external Python dependencies**.

Why it wins review and wins deals: this is not a bare connector. It is the
governance layer enterprises need before they will let an AI near their ERP.

One-click, modern connect (OAuth 2.1 + PKCE)
--------------------------------------------
* "Sign in with Odoo" — the connect flow Anthropic and OpenAI recommend. No API
  keys, no config files, no ``mcp-remote``. Paste one URL and click *Allow*.
* Full self-contained Authorization Server: **Client ID Metadata Documents**
  (the registration mechanism the MCP spec recommends — the user never sees a
  client ID or secret), RFC 9728 protected-resource metadata, RFC 8414 server
  metadata, PKCE S256, RFC 8707 audience-bound tokens, RFC 9207 issuer
  identification, RFC 7009 revocation, and refresh-token rotation. RFC 7591
  dynamic registration is retained as a deprecated fallback.
* Users authenticate with their normal Odoo login — SSO and 2FA just work.

Works with both generations of MCP client
-----------------------------------------
The transport is **dual-era**: protocol revision ``2026-07-28`` (per-request
metadata, ``server/discover``, no handshake) *and* the older handshake-based
revisions (``2025-06-18``, ``2025-03-26``). Connectors built against either
generation keep working.

Runs as the real user — never a shared admin token
--------------------------------------------------
Every request executes **as the signed-in Odoo user**, so ``ir.model.access``,
record rules, field groups and multi-company isolation all apply underneath the
MCP scope. A read-only user can only read; a user without delete rights cannot
delete. The AI can never exceed the person using it.

Governance that closes the deal
-------------------------------
* **Scopes** — per-model read/create/write/delete, field blacklists, extra
  record domains and row caps. Read-only by default.
* **Approval gate** — mutating calls become a human approval request instead of
  executing. Approve/reject from a kanban, executed as the original user.
* **Audit log** — one attributable row per call: user, tool, model, IP,
  duration and a token estimate for cost tracking. Configurable retention.
* **Rate limiting** — per-connection calls/hour ceiling.

More than a connector
---------------------
* **Capability & tool registry** — tools are data; partners ship new ones
  without touching code.
* **Business Context Engine** — teach the AI what your data *means*, so it
  reasons about your business, not just your columns.
* **Prompt library** — curated, role-based prompts (CFO, Sales Manager,
  Warehouse Manager…) appear natively in the client's picker.
* **Reports & dashboards** — a governed ``read_group`` aggregation tool powers
  "revenue by month" and "open leads by salesperson" answers.

Provider-agnostic by design. Multi-company aware. Fully translatable.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke",
    "maintainer": "Fleet",
    "license": "OPL-1",  # Odoo Proprietary License (paid App Store module)
    "price": 299.00,
    "currency": "USD",
    "depends": ["base", "web", "mail"],
    "data": [
        # security (groups + rules first, then the access matrix)
        "security/mcp_security.xml",
        "security/ir.model.access.csv",
        # seed data (capabilities before prompts that reference them)
        "data/mcp_capability_data.xml",
        "data/mcp_prompt_data.xml",
        "data/mcp_scope_data.xml",
        "data/ir_cron.xml",
        # web / OAuth consent templates
        "views/oauth_templates.xml",
        # backend views + actions. The matrix/picker load first: the scope form
        # has a button that references the picker action by xml id.
        "views/mcp_model_access_views.xml",
        "views/mcp_views.xml",
        "views/mcp_registry_views.xml",
        "views/mcp_oauth_views.xml",
        "views/mcp_connect_views.xml",
        "wizard/mcp_wizard_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        # menus last (they reference the actions defined above)
        "views/mcp_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mcp_governance_suite/static/src/connect/connect.scss",
            "mcp_governance_suite/static/src/connect/connect.js",
            "mcp_governance_suite/static/src/connect/connect.xml",
        ],
    },
    "demo": [
        "demo/mcp_demo.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
}
