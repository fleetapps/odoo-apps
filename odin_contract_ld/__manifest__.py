# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
#
# LGPL-3, and deliberately: this depends only on Odoo core, so it carries no
# AGPL section 13 network clause and an OPL-1 ODIN app may depend on it. Keep
# `depends` free of AGPL modules or that stops being true.
{
    "name": "Contract Liquidated Damages",
    "summary": "Assess, cap and deduct liquidated damages on EPC contracts",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["account", "analytic", "project", "purchase", "sale"],
    "data": [
        "security/odin_contract_ld_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/odin_ld_agreement_views.xml",
        "views/odin_ld_eot_views.xml",
        "views/odin_ld_charge_views.xml",
        "views/account_move_views.xml",
        "views/project_views.xml",
        "wizard/odin_ld_apply_wizard_views.xml",
        "report/odin_ld_report_actions.xml",
        "report/odin_ld_assessment_template.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}
