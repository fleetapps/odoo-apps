# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
#
# LGPL-3 with core-only dependencies. In particular this does NOT depend on
# mis_builder_budget: that is AGPL, and depending on it would stop an OPL-1
# ODIN app being able to depend on this one. The budget model here is
# deliberately small and purpose-built for cost control by workstream.
{
    "name": "Committed Cost Control",
    "summary": "Budget vs committed vs actual by project and workstream",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["account", "analytic", "project", "purchase"],
    "data": [
        "security/odin_committed_cost_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/odin_commitment_views.xml",
        "views/odin_cost_budget_views.xml",
        "views/odin_cost_control_report_views.xml",
        "views/res_config_settings_views.xml",
        "views/purchase_order_views.xml",
        "views/project_views.xml",
        "report/odin_cost_control_report_actions.xml",
        "report/odin_cost_control_template.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}
