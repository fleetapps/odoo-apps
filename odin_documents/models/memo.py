from odoo import _, api, fields, models
from odoo.exceptions import UserError

# What may still change on an issued memo: it is an official record.
ISSUED_WRITABLE = {"state", "ack_ids", "message_main_attachment_id", "activity_ids", "message_follower_ids",
                   "message_ids", "website_message_ids"}


class DocumentMemo(models.Model):
    """An internal memo: written by a department, issued by a manager with its
    number, then locked. Staff acknowledge receipt where it asks them to."""

    _name = "odin.document.memo"
    _inherit = ["mail.thread", "odin.document.mixin"]
    _description = "Memo"
    _order = "date desc, id desc"

    name = fields.Char(string="Number", default=lambda self: _("Draft"), readonly=True, copy=False, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    department_id = fields.Many2one("hr.department", string="Department", required=True, tracking=True)
    to = fields.Char(string="To", default=lambda self: _("All staff"), required=True)
    subject = fields.Char(required=True, tracking=True)
    body = fields.Html(string="Content")
    author_id = fields.Many2one("res.users", string="From", default=lambda self: self.env.user, required=True)
    approved_by_id = fields.Many2one("res.users", string="Authorised by", readonly=True, copy=False)
    issued_on = fields.Datetime(readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("issued", "Issued"), ("cancelled", "Cancelled")],
                             default="draft", required=True, readonly=True, tracking=True, copy=False)
    needs_acknowledgement = fields.Boolean(string="Staff acknowledge receipt")
    ack_ids = fields.One2many("odin.document.memo.ack", "memo_id", string="Acknowledgements", readonly=True)
    acknowledged = fields.Boolean(compute="_compute_acknowledged", string="I acknowledged")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    @api.depends_context("uid")
    @api.depends("ack_ids.user_id")
    def _compute_acknowledged(self):
        for memo in self:
            memo.acknowledged = self.env.user in memo.ack_ids.user_id

    def write(self, vals):
        if self.filtered(lambda m: m.state != "draft") and set(vals) - ISSUED_WRITABLE:
            raise UserError(_("An issued memo is an official record and cannot be changed. Cancel it and write a new one."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_drafts(self):
        if self.filtered(lambda m: m.state != "draft"):
            raise UserError(_("Only a draft memo can be deleted; an issued one is cancelled instead."))

    def _next_number(self):
        self.ensure_one()
        sequence = self.env["ir.sequence"].sudo().next_by_code("odin.document.memo", sequence_date=self.date)
        short = self.company_id._doc_short()
        return f"{short}/MEMO/{self.department_id._doc_code()}/{self.date.year}/{sequence}"

    def action_issue(self):
        if not self.env.user.has_group("odin_documents.group_documents_manager"):
            raise UserError(_("Only a Documents manager can authorise and issue a memo."))
        for memo in self.filtered(lambda m: m.state == "draft"):
            if not memo.body:
                raise UserError(_("Write the memo before issuing it."))
            memo.write({"name": memo._next_number(), "state": "issued", "approved_by_id": self.env.user.id,
                        "issued_on": fields.Datetime.now()})
        return True

    def action_cancel(self):
        if not self.env.user.has_group("odin_documents.group_documents_manager"):
            raise UserError(_("Only a Documents manager can cancel a memo."))
        self.filtered(lambda m: m.state != "cancelled").write({"state": "cancelled"})
        return True

    def action_acknowledge(self):
        for memo in self:
            if memo.state != "issued":
                raise UserError(_("Only an issued memo can be acknowledged."))
            if self.env.user not in memo.ack_ids.user_id:
                self.env["odin.document.memo.ack"].sudo().create({"memo_id": memo.id, "user_id": self.env.user.id})
        return True

    def action_print(self):
        return self.env.ref("odin_documents.action_report_memo").report_action(self)


class DocumentMemoAck(models.Model):
    _name = "odin.document.memo.ack"
    _description = "Memo acknowledgement"
    _order = "date"

    memo_id = fields.Many2one("odin.document.memo", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="Staff", required=True, default=lambda self: self.env.user)
    date = fields.Datetime(default=fields.Datetime.now, required=True)

    _memo_user_unique = models.Constraint("UNIQUE(memo_id, user_id)", "Each person acknowledges a memo once.")
