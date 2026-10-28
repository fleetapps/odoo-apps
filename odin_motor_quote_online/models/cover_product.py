from odoo import api, models


class CoverProduct(models.Model):
    _inherit = "odin.cover.product"

    @api.model
    def _odin_motor_shared(self):
        # Shown online or not: the same for every band of a product.
        return super()._odin_motor_shared() + ["odin_online"]
