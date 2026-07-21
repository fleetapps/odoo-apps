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
        months = int(self.env["ir.config_parameter"].sudo().get_param(
            "mcp_governance_suite.retention_months", "12"))
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        self.search([("create_date", "<", cutoff)]).unlink()
