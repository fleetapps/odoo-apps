# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "M-Pesa Statements: Pull API and Org Portal",
    "summary": "Backfill missed M-Pesa receipts from the Pull API (48 hours) and "
    "the M-PESA Org Portal statement; book charges and sweeps; settlements "
    "and charges per till per month",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": [
        "x_ke_mpesa",
        "account_statement_import_online",
        "account_statement_import_sheet_file",
    ],
    "data": [
        "security/x_ke_mpesa_statement_security.xml",
        "security/ir.model.access.csv",
        "data/account_statement_import_sheet_mapping_data.xml",
        "data/queue_job_data.xml",
        "views/mpesa_shortcode_views.xml",
        "views/account_statement_import_sheet_mapping_views.xml",
        "views/account_bank_statement_views.xml",
        "views/res_config_settings_views.xml",
        "report/mpesa_report_settlement_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
}
