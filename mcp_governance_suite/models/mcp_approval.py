# -*- coding: utf-8 -*-
import json
from odoo import fields, models


class MCPApprovalRequest(models.Model):
    _name = "mcp.approval.request"
    _description = "MCP Write Approval"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    api_key_id = fields.Many2one("mcp.api.key", required=True)
    operation = fields.Selection([("create", "Create"), ("write", "Write"),
                                  ("unlink", "Delete")], required=True)
    model_name = fields.Char(required=True)
    record_id = fields.Integer()
    values_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("approved", "Approved"),
         ("rejected", "Rejected"), ("executed", "Executed"),
         ("failed", "Failed")], default="pending", tracking=True)

    def action_approve(self):
        for req in self:
            values = json.loads(req.values_json or "{}")
            # Execute AS the key's user so ACLs/ir.rules still apply
            env_as = self.env(user=req.api_key_id.user_id.id)
            try:
                Model = env_as[req.model_name]
                if req.operation == "create":
                    Model.create(values)
                elif req.operation == "write":
                    Model.browse(req.record_id).write(values)
                elif req.operation == "unlink":
                    Model.browse(req.record_id).unlink()
                req.state = "executed"
            except Exception as e:  # noqa: BLE001
                req.state = "failed"
                req.message_post(body=f"Execution failed: {e}")

    def action_reject(self):
        self.write({"state": "rejected"})
