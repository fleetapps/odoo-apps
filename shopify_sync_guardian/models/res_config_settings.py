# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    guardian_slack_webhook = fields.Char(
        config_parameter="shopify_sync_guardian.slack_webhook",
        string="Slack Webhook URL")
    guardian_alert_emails = fields.Char(
        config_parameter="shopify_sync_guardian.alert_emails",
        string="Alert Emails (comma-separated)")
    guardian_max_retries = fields.Char(
        config_parameter="shopify_sync_guardian.max_retries",
        string="Max Auto-Retries", default="3")
