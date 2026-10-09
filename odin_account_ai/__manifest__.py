# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Accounting AI",
    "summary": "An AI menu in Accounting: ask the ledger, explain any P&L figure, "
    "review transactions, find anomalies and draft journal entries, with Claude",
    "description": """
Adds Accounting > AI, built on Claude (Anthropic): Ask the Ledger (questions
answered from the books with clickable evidence), Explain on any figure of the
interactive P&L, Transaction Review (suggested accounts for bank lines and
draft bills, approved by an accountant), Anomalies (scheduled checks with an
inbox) and Draft Entry (a journal entry from a sentence, validated before it
is created). The AI never posts or changes anything on its own. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_account_pnl", "mail"],
    # The official Anthropic SDK; pinned in the repository's requirements.txt.
    "external_dependencies": {"python": ["anthropic"]},
    "data": [
        "security/odin_ai_security.xml",
        "security/ir.model.access.csv",
        "data/odin_ai_detector_data.xml",
        "data/ir_cron_data.xml",
        "views/odin_ai_run_views.xml",
        # After the run views: the settings link to the activity log.
        "views/res_config_settings_views.xml",
        "views/odin_ai_finding_views.xml",
        "views/odin_ai_draft_views.xml",
        "views/odin_ai_rule_views.xml",
        "views/odin_ai_actions.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_account_ai/static/src/**/*",
        ],
        "web.assets_tests": [
            "odin_account_ai/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
