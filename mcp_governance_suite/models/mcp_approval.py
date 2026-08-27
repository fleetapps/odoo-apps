# -*- coding: utf-8 -*-
"""Human-in-the-loop approval gate for AI-initiated writes.

When a scope has "require approval" on, mutating tool calls do not execute -
they land here as a pending request an authorised human reviews. Approval then
runs the operation *as the original user*, so ACLs and record rules are checked
a second time at execution. This is what lets a business say "yes, the AI can
draft, but a person confirms".
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MCPApprovalRequest(models.Model):
    _name = "mcp.approval.request"
    _description = "MCP Write Approval"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(compute="_compute_name")
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade",
        help="The user the operation will run as when approved.")
    scope_id = fields.Many2one("mcp.scope", ondelete="set null")
    api_key_id = fields.Many2one("mcp.api.key", ondelete="set null")
    operation = fields.Selection(
        [("create", "Create"), ("write", "Write"), ("unlink", "Delete"),
         ("call_method", "Method Call")],
        required=True, tracking=True)
    model_name = fields.Char(required=True, tracking=True)
    record_id = fields.Integer()
    values_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("approved", "Approved"),
         ("rejected", "Rejected"), ("executed", "Executed"),
         ("failed", "Failed")], default="pending", tracking=True, index=True)
    result_ref = fields.Char(readonly=True, help="Record created/affected on execution.")

    @api.depends("operation", "model_name", "record_id")
    def _compute_name(self):
        for rec in self:
            suffix = (" #%s" % rec.record_id) if rec.record_id else ""
            rec.name = "%s %s%s" % (
                (rec.operation or "").title(), rec.model_name or "", suffix)

    def _notify_approvers(self):
        """Ping the MCP Approver group via an activity so nothing is missed."""
        group = self.env.ref("mcp_governance_suite.group_mcp_approver", raise_if_not_found=False)
        if not group:
            return
        # Odoo 19 removed res.groups.users; all_user_ids is the right successor
        # here because a user who only holds an *implying* group is still an
        # approver and would otherwise never be notified.
        members = group.all_user_ids if "all_user_ids" in group._fields \
            else group.users
        for req in self:
            # The requester is subscribed too. They are the one person who is
            # certain to be waiting on the answer, and without this they are
            # the only participant who never learns whether it was approved,
            # rejected or failed - their assistant said "queued" and then the
            # thread went silent for them alone.
            partners = members.partner_id | req.user_id.partner_id
            req.message_subscribe(partner_ids=partners.ids)
            req.message_post(
                body=_("AI requested a %s on %s. Awaiting approval.")
                % (req.operation, req.model_name),
                subtype_xmlid="mail.mt_comment")

    def action_approve(self):
        for req in self.filtered(lambda r: r.state == "pending"):
            values = json.loads(req.values_json or "{}")
            # Execute AS the requesting user so ACLs/ir.rules apply again.
            env_as = self.env(user=req.user_id.id)
            try:
                Model = env_as[req.model_name]
                if req.operation == "create":
                    rec = Model.create(values)
                    req.result_ref = "%s,%s" % (req.model_name, rec.id)
                elif req.operation == "write":
                    Model.browse(req.record_id).write(values)
                    req.result_ref = "%s,%s" % (req.model_name, req.record_id)
                elif req.operation == "unlink":
                    Model.browse(req.record_id).unlink()
                elif req.operation == "call_method":
                    req._execute_method(Model, values)
                req.state = "executed"
                req.message_post(body=_("Approved and executed by %s.") % self.env.user.name)
            except Exception as exc:  # noqa: BLE001 - surfaced on the request
                req.state = "failed"
                req.message_post(body=_("Execution failed: %s") % exc)

    def _execute_method(self, Model, values):
        """Run an approved method call, re-checking the allow-list first.

        The matrix may have changed between the request and the approval - an
        admin may have revoked exactly this method in the meantime. Approving
        an old request must not resurrect a permission that no longer exists.
        """
        self.ensure_one()
        method = (values.get("method") or "").strip()
        line = self.scope_id.line_for_model(self.model_name) if self.scope_id else None
        if not line or not line.can_call_methods or \
                method not in line.allowed_method_set():
            raise UserError(_(
                "'%(method)s' is no longer allow-listed on %(model)s, so this "
                "request can no longer be executed. Reject it instead.",
                method=method, model=self.model_name))
        records = Model.browse(values.get("record_ids") or [])
        getattr(records, method)(**(values.get("kwargs") or {}))
        self.result_ref = "%s,%s" % (self.model_name, self.record_id or 0)

    def action_reject(self):
        self.filtered(lambda r: r.state == "pending").write({"state": "rejected"})
        for req in self:
            req.message_post(body=_("Rejected by %s.") % self.env.user.name)
