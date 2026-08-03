# Manifest reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "MCP Governance Suite",
    "version": "19.0.1.0.0",
    "category": "Extra Tools/AI",
    "summary": "Model Context Protocol server for Odoo with the governance layer: "
               "per-user tool scoping, full audit log, read-only mode, approval "
               "gates for writes, token cost tracking.",
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
    "installable": True,
    "application": True,
}
