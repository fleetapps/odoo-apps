# -*- coding: utf-8 -*-
from odoo import api, fields, models


class GuardianIssue(models.Model):
    _name = "guardian.issue"
    _description = "Sync Issue"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(required=True)
    kind = fields.Selection(
        [("failed_order", "Failed Order"), ("failed_job", "Failed Job"),
         ("stuck", "Stuck Queue"), ("orphan", "Orphan Record"),
         ("duplicate", "Duplicate")], required=True, index=True)
    res_model = fields.Char(index=True)
    res_id = fields.Integer(index=True)
    error = fields.Text()
    state = fields.Selection(
        [("open", "Open"), ("retrying", "Retrying"),
         ("resolved", "Resolved"), ("ignored", "Ignored")],
        default="open", tracking=True, index=True)
    retry_count = fields.Integer(default=0)
    last_retry = fields.Datetime()

    _sql_constraints = [
        ("uniq_ref", "unique(res_model, res_id, kind)",
         "This sync record is already tracked."),
    ]

    # ---------------- crons ----------------
    @api.model
    def cron_scan(self):
        Adapter = self.env["guardian.adapter"]
        seen = 0
        for item in Adapter.scan_failed():
            if not self.search_count([("res_model", "=", item["res_model"]),
                                      ("res_id", "=", item["res_id"]),
                                      ("kind", "=", item["kind"]),
                                      ("state", "in", ("open", "retrying"))]):
                self.create(item)
                seen += 1
        if seen:
            self.env["guardian.alert"].notify_new_issues(seen)

    @api.model
    def cron_auto_retry(self):
        max_retries = int(self.env["ir.config_parameter"].sudo().get_param(
            "shopify_sync_guardian.max_retries", "3"))
        for issue in self.search([("state", "in", ("open", "retrying")),
                                  ("retry_count", "<", max_retries)], limit=100):
            ok = self.env["guardian.adapter"].retry(issue)
            issue.write({
                "retry_count": issue.retry_count + 1,
                "last_retry": fields.Datetime.now(),
                "state": "resolved" if ok else "retrying",
            })
        # escalate exhausted issues
        exhausted = self.search([("state", "=", "retrying"),
                                 ("retry_count", ">=", max_retries)])
        if exhausted:
            self.env["guardian.alert"].notify_exhausted(exhausted)

    def action_retry_now(self):
        for issue in self:
            ok = self.env["guardian.adapter"].retry(issue)
            issue.state = "resolved" if ok else issue.state

    def action_ignore(self):
        self.write({"state": "ignored"})
