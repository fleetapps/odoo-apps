# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import api, fields, models


class MCPAuditLog(models.Model):
    _name = "mcp.audit.log"
    _description = "MCP Audit Log"
    _order = "create_date desc"

    api_key_id = fields.Many2one("mcp.api.key", index=True)
    user_id = fields.Many2one("res.users", index=True)
    tool = fields.Char(index=True)
    args_json = fields.Text()
    status = fields.Selection([("ok", "OK"), ("error", "Error")], index=True)
    duration_ms = fields.Integer()
    tokens_est = fields.Integer(help="Rough size-based token estimate for cost tracking.")

    @api.model
    def cron_purge(self):
        # Was reading `retention_months`, a parameter nothing ever wrote, so
        # every install silently purged at the 12-month fallback. Now driven by
        # Settings > MCP Governance > Audit; 0 means keep forever.
        days = self.env["mcp.config"].get("log_retention_days", 365)
        if not days:
            return
        cutoff = fields.Datetime.now() - relativedelta(days=days)
        self.search([("create_date", "<", cutoff)]).unlink()
