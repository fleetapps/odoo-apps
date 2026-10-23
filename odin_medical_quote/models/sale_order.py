from odoo import api, models
from odoo.tools.misc import formatLang


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.depends("name", "sale_order_template_id", "amount_total")
    @api.depends_context("odin_plan_label")
    def _compute_display_name(self):
        """``APA Jamii Plus – Family Cover (KES 500,000) · KES 47,862.24 · S00022``
        where a plan is being picked: an order number alone does not tell a
        salesperson which plan the client said yes to."""
        if not self.env.context.get("odin_plan_label"):
            return super()._compute_display_name()
        for order in self:
            order.display_name = " · ".join(filter(None, [
                order.sale_order_template_id.name,
                formatLang(self.env, order.amount_total, currency_obj=order.currency_id),
                order.name,
            ]))

    def _action_confirm(self):
        """A plan of a medical quote confirmed is the client's choice, however
        it was confirmed -- Client accepted, Confirm here, or signed on the
        portal -- so the quote, its other plans and the opportunity follow.

        In sudo: the portal confirms as the public user, and a salesperson who
        may confirm the order may also close out the quote it came from."""
        result = super()._action_confirm()
        comparisons = self.env["odin.quote.comparison"].sudo().search([
            ("plan_ids.order_id", "in", self.ids),
            ("state", "!=", "accepted"),
        ])
        for comparison in comparisons.filtered("odin_from_builder"):
            order = (comparison.plan_ids.order_id & self)[:1]
            comparison._odin_accept(order.sudo())
        return result
