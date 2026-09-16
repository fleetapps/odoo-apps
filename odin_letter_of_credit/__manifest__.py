# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
#
# LGPL-3 with core-only dependencies, so an OPL-1 ODIN app may depend on it.
# Adding an AGPL module to `depends` would end that.
{
    "name": "Letters of Credit",
    "summary": "Documentary credits: register, document checklist, utilisations "
    "and payment released only against a complete document set",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["account", "analytic", "project", "purchase"],
    "data": [
        "security/odin_lc_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/odin_lc_document_type_data.xml",
        "data/ir_cron_data.xml",
        "views/odin_lc_document_type_views.xml",
        "views/odin_letter_of_credit_views.xml",
        "views/odin_lc_utilisation_views.xml",
        "views/odin_lc_amendment_views.xml",
        "views/odin_lc_charge_views.xml",
        "views/account_move_views.xml",
        "views/purchase_order_views.xml",
        "views/project_views.xml",
        "wizard/odin_lc_amend_wizard_views.xml",
        "report/odin_lc_report_actions.xml",
        "report/odin_lc_status_template.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}
