# -*- coding: utf-8 -*-
"""Alerting: email (via mail.thread message_post / mail.mail) + Slack webhook.

External API calls from server code use `requests` which ships with Odoo's
python environment. Config params stored in ir.config_parameter.
"""
import json
import logging
import requests
from odoo import api, models

_logger = logging.getLogger(__name__)


class GuardianAlert(models.AbstractModel):
    _name = "guardian.alert"
    _description = "Guardian Alerts"

    @api.model
    def _slack(self, text):
        url = self.env["ir.config_parameter"].sudo().get_param(
            "shopify_sync_guardian.slack_webhook")
        if not url:
            return
        try:
            requests.post(url, data=json.dumps({"text": text}),
                          headers={"Content-Type": "application/json"}, timeout=10)
        except Exception as e:  # noqa: BLE001
            _logger.warning("Slack alert failed: %s", e)

    @api.model
    def _email(self, subject, body):
        emails = self.env["ir.config_parameter"].sudo().get_param(
            "shopify_sync_guardian.alert_emails")
        if not emails:
            return
        self.env["mail.mail"].sudo().create({
            "subject": subject, "body_html": body, "email_to": emails,
        }).send()

    @api.model
    def notify_new_issues(self, count):
        msg = f"Shopify Sync Guardian: {count} new sync issue(s) detected."
        self._slack(msg)
        self._email("Sync issues detected", f"<p>{msg}</p>")

    @api.model
    def notify_exhausted(self, issues):
        names = ", ".join(issues.mapped("name")[:10])
        msg = (f"Shopify Sync Guardian: {len(issues)} issue(s) exhausted "
               f"auto-retries and need attention: {names}")
        self._slack(msg)
        self._email("Sync issues need attention", f"<p>{msg}</p>")
