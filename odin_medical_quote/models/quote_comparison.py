from odoo import _, api, fields, models
from odoo.exceptions import UserError


class QuoteComparison(models.Model):
    """A cover comparison is the medical quote's record: the plans offered,
    who they were priced for, the opportunity they belong to, and -- once the
    client has chosen -- the plan they accepted."""

    _inherit = "odin.quote.comparison"

    state = fields.Selection(
        selection_add=[("accepted", "Accepted")],
        ondelete={"accepted": "set default"},
    )
    opportunity_id = fields.Many2one(
        "crm.lead", string="Opportunity", index=True, tracking=True,
        ondelete="set null", copy=False,
    )
    odin_builder_params = fields.Json(
        string="Quote builder input", copy=False,
        help="What was entered in Add Medical Quote, so Edit quote reopens it "
        "as it was left.",
    )
    odin_from_builder = fields.Boolean(compute="_compute_odin_from_builder")
    odin_accepted_order_id = fields.Many2one(
        "sale.order", string="Accepted quotation", readonly=True, copy=False,
    )
    odin_invoice_count = fields.Integer(compute="_compute_odin_invoice_count")

    @api.depends("odin_builder_params")
    def _compute_odin_from_builder(self):
        for comparison in self:
            comparison.odin_from_builder = bool(comparison.odin_builder_params)

    @api.depends("odin_accepted_order_id.invoice_ids")
    def _compute_odin_invoice_count(self):
        for comparison in self:
            comparison.odin_invoice_count = len(comparison.odin_accepted_order_id.invoice_ids)

    def _odin_builder_action(self, **params):
        return {
            "type": "ir.actions.client",
            "tag": "odin_medical_quote",
            "name": _("Add Medical Quote"),
            "params": params,
        }

    def action_odin_edit(self):
        """Reopen the quote in Add Medical Quote, as it was saved."""
        self.ensure_one()
        if self.state == "accepted":
            raise UserError(_("%s has been accepted and can no longer change.", self.name))
        return self._odin_builder_action(comparison_id=self.id)

    def action_odin_accept(self):
        """The client has chosen: confirm their plan and invoice it."""
        self.ensure_one()
        if not self.plan_ids:
            raise UserError(_("There is no plan on this quote to accept."))
        orders = self.plan_ids.order_id.filtered(lambda o: o.state != "cancel")
        recommended = self.plan_ids.filtered("highlight").order_id[:1]
        return {
            "type": "ir.actions.act_window",
            "name": _("Client accepted"),
            "res_model": "odin.medical.quote.accept",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_comparison_id": self.id,
                "default_order_id": (recommended or orders[:1]).id,
                "odin_plan_label": True,
            },
        }

    def action_odin_open_opportunity(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": self.opportunity_id.id,
            "views": [(False, "form")],
        }

    def action_odin_view_invoices(self):
        self.ensure_one()
        return self.odin_accepted_order_id.action_view_invoice()

    @api.model
    def action_odin_new_quote(self):
        return self._odin_builder_action()
