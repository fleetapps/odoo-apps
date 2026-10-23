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
