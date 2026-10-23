from odoo import api, models


class MotorQuote(models.AbstractModel):
    _inherit = "odin.motor.quote"

    @api.model
    def _odin_rate_domain(self, vehicle):
        """Online, only the rates and vehicle types shown to the public."""
        domain = super()._odin_rate_domain(vehicle)
        if self.env.context.get("odin_online"):
            domain += [("odin_online", "=", True), ("class_id.odin_online", "=", True)]
        return domain
