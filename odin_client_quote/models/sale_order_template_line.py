from odoo import fields, models


class SaleOrderTemplateLine(models.Model):
    """Carry the client-report fields from the template onto the quotation.

    ``_prepare_order_line_values`` is the single place where a template line
    becomes an order line, and it copies an explicit list of fields. Anything
    added to ``sale.order.line`` alone is silently dropped the moment a
    salesperson picks a template, so the same three fields live here too.
    """

    _inherit = "sale.order.template.line"

    cover_limit = fields.Monetary(
        string="Cover limit",
        currency_field="currency_id",
        help="What this benefit pays out. Set it on the section line.",
    )
    benefit_label = fields.Char(
        string="Benefit name",
        help="Short name for this section in client reports, e.g. 'Outpatient'.",
    )
    benefit_key = fields.Char(
        string="Benefit key",
        help="Matches this section to the same benefit on other plans in a "
        "cover comparison, e.g. 'inpatient'.",
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True
    )

    def _prepare_order_line_values(self):
        vals = super()._prepare_order_line_values()
        vals.update({
            "cover_limit": self.cover_limit,
            "benefit_label": self.benefit_label,
            "benefit_key": self.benefit_key,
        })
        return vals
