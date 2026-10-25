# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mpesa_suspense_account_id = fields.Many2one(
        related="company_id.mpesa_suspense_account_id", readonly=False)
    mpesa_rounding_account_id = fields.Many2one(
        related="company_id.mpesa_rounding_account_id", readonly=False)
    mpesa_match_stk = fields.Boolean(related="company_id.mpesa_match_stk", readonly=False)
    mpesa_match_claims = fields.Boolean(related="company_id.mpesa_match_claims", readonly=False)
    mpesa_match_paybill = fields.Boolean(related="company_id.mpesa_match_paybill", readonly=False)
    mpesa_claim_window = fields.Integer(related="company_id.mpesa_claim_window", readonly=False)
    mpesa_manual_alert_minutes = fields.Integer(
        related="company_id.mpesa_manual_alert_minutes", readonly=False)
    mpesa_supervisor_id = fields.Many2one(
        related="company_id.mpesa_supervisor_id", readonly=False)
    mpesa_phone_retention_days = fields.Integer(
        related="company_id.mpesa_phone_retention_days", readonly=False)
    mpesa_transaction_status_check = fields.Boolean(
        related="company_id.mpesa_transaction_status_check", readonly=False)
    mpesa_ip_allowlist_enabled = fields.Boolean(
        "Only trust callbacks from Safaricom's addresses",
        config_parameter="x_ke_mpesa.ip_allowlist_enabled")
    mpesa_ip_allowlist = fields.Char(
        "Safaricom callback addresses", config_parameter="x_ke_mpesa.ip_allowlist",
        default="196.201.212.0/24,196.201.213.0/24,196.201.214.0/24",
        help="Comma-separated addresses or networks. The default is the "
             "community-documented list: confirm it with Safaricom.")
    mpesa_callback_base_url = fields.Char(
        "Callback base URL", config_parameter="x_ke_mpesa.callback_base_url",
        help="The public https:// address Safaricom calls, when it is not "
             "web.base.url. It must not contain mpesa, safaricom, exe, cmd, sql "
             "or query, and must lead to this database alone.")
