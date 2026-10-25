# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
#
# LGPL-3, but account_reconcile_oca and its statement base are AGPL-3/LGPL-3
# OCA modules, so an OPL-1 app may NOT depend on this one (the rule in
# x_ke_base and odin_letter_of_credit). Anything that has to join M-Pesa to an
# OPL app does it in a bridge that depends on both.
{
    "name": "M-Pesa (Daraja) for Accounting",
    "summary": "Every M-Pesa till and paybill as a bank journal: one statement "
    "line per receipt from C2B, STK Push, Pull or statement, matched to "
    "invoices and POS sessions, with STK Push from invoices",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": [
        "account",
        "mail",
        "queue_job",
        "account_statement_base",
        # unique_import_id lives here in 19.0, not in core account
        "account_statement_import_base",
        "account_reconcile_oca",
    ],
    "external_dependencies": {"python": ["cryptography", "requests"]},
    "data": [
        "security/x_ke_mpesa_security.xml",
        "security/ir.model.access.csv",
        "data/queue_job_data.xml",
        "data/ir_cron_data.xml",
        "data/mail_activity_type_data.xml",
        "views/mpesa_app_views.xml",
        "views/mpesa_shortcode_views.xml",
        "views/mpesa_transaction_views.xml",
        "views/mpesa_callback_views.xml",
        "views/account_journal_views.xml",
        "views/account_move_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/mpesa_stk_request_views.xml",
        "wizard/mpesa_allocate_views.xml",
        "report/mpesa_report_views.xml",
        # Menus last: they reference the actions defined above.
        "views/x_ke_mpesa_menus.xml",
    ],
    "installable": True,
    "application": False,
}
