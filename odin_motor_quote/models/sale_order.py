from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    odin_motor_rate_id = fields.Many2one(
        "odin.motor.rate", string="Motor rate", readonly=True, copy=False, index="btree_not_null",
        help="The insurer's rate this motor quotation was priced from.")

    def _odin_plan_label(self):
        self.ensure_one()
        rate = self.odin_motor_rate_id
        if rate:
            return "%s – %s" % (rate.insurer_id.name, dict(rate._fields["cover"]._description_selection(
                self.env)).get(rate.cover))
        return super()._odin_plan_label()

    def _odin_insurer_label(self):
        self.ensure_one()
        return self.odin_motor_rate_id.insurer_id.name or super()._odin_insurer_label()

    def _odin_policy_months(self):
        self.ensure_one()
        rate = self.odin_motor_rate_id
        return rate.period_months if rate else super()._odin_policy_months()

    def _odin_insurer_partner(self):
        self.ensure_one()
        return self.odin_motor_rate_id.insurer_id or super()._odin_insurer_partner()

    def _odin_cover_product(self):
        self.ensure_one()
        return self.odin_motor_rate_id.product_id or super()._odin_cover_product()

    def _odin_plan_name(self):
        self.ensure_one()
        rate = self.odin_motor_rate_id
        if rate:
            return dict(rate._fields["cover"]._description_selection(self.env)).get(rate.cover)
        return super()._odin_plan_name()
