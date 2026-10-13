"""Optional benefits, picked across every plan on one screen.

A telesales agent on a call is told "they also want optical". The benefit is
already in each plan's quotation at quantity zero -- 479 of the 525 lines in
the rate cards are -- so adding it means setting a quantity to one, not adding
a product. By hand that is three or four quotations to open, each with the
right line to find and edit, while somebody waits on the phone.

One row per option the plans actually offer, grouped by benefit. Not one row
per benefit: an optional section holds several products, not one -- four or
five tiers of optical, and twenty-eight riders -- so a single tick could not
say which. Where a plan does not carry a benefit at all it gets a row that
says so and cannot be ticked, because that is usually the honest answer and
worth the client seeing.

Nothing is created or deleted: only quantities change, so the comparison, its
totals and the printed PDF all follow on their own.
"""

from odoo import _, api, fields, models


class QuoteAddon(models.TransientModel):
    _name = "odin.quote.addon"
    _description = "Pick optional benefits for every plan"

    comparison_id = fields.Many2one(
        "odin.quote.comparison", required=True, readonly=True, ondelete="cascade",
    )
    line_ids = fields.One2many("odin.quote.addon.line", "wizard_id", string="Options")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        comparison = self.env["odin.quote.comparison"].browse(
            values.get("comparison_id") or self.env.context.get("default_comparison_id"))
        if comparison.exists():
            values["comparison_id"] = comparison.id
            values["line_ids"] = [(0, 0, row) for row in comparison._addon_rows()]
        return values

    def action_apply(self):
        """Set the quantities the ticks ask for, then go back to the comparison."""
        self.ensure_one()
        added = removed = 0
        for line in self.line_ids.filtered("order_line_id"):
            wanted = 1.0 if line.included else 0.0
            if line.order_line_id.product_uom_qty == wanted:
                continue
            line.order_line_id.product_uom_qty = wanted
            added += 1 if wanted else 0
            removed += 0 if wanted else 1
        if added or removed:
            self.comparison_id.message_post(body=_(
                "Optional benefits: %(added)s added, %(removed)s removed.",
                added=added, removed=removed))
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.quote.comparison",
            "res_id": self.comparison_id.id,
            "views": [(False, "form")],
            "target": "current",
        }


class QuoteAddonLine(models.TransientModel):
    _name = "odin.quote.addon.line"
    _description = "One optional benefit option on one plan"
    _order = "benefit_sequence, plan_sequence, price, id"

    wizard_id = fields.Many2one("odin.quote.addon", required=True, ondelete="cascade", index=True)
    benefit_label = fields.Char("Benefit", readonly=True)
    benefit_sequence = fields.Integer(readonly=True)
    plan_name = fields.Char("Plan", readonly=True)
    plan_sequence = fields.Integer(readonly=True)
    order_line_id = fields.Many2one("sale.order.line", readonly=True)
    option_name = fields.Char("Option", readonly=True)
    cover_limit = fields.Monetary(readonly=True, currency_field="currency_id")
    price = fields.Monetary("Premium", readonly=True, currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", readonly=True)
    available = fields.Boolean(readonly=True, default=True)
    included = fields.Boolean("Include")

    @api.onchange("included")
    def _onchange_included(self):
        """A plan the insurer does not sell the benefit on cannot be ticked."""
        for line in self:
            if line.included and not line.available:
                line.included = False
                return {"warning": {
                    "title": _("Not on this plan"),
                    "message": _(
                        "%(plan)s does not carry %(benefit)s. The insurer does not sell it "
                        "on that product, so it cannot be quoted here -- offer a plan that "
                        "includes it instead.",
                        plan=line.plan_name, benefit=line.benefit_label),
                }}
