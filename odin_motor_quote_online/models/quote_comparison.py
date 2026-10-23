from odoo import models


class QuoteComparison(models.Model):
    _inherit = "odin.quote.comparison"

    def _odin_online_models(self):
        return dict(super()._odin_online_models(), motor="odin.motor.quote.online")
