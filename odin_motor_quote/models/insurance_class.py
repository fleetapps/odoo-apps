from odoo import api, fields, models


class InsuranceClass(models.Model):
    _inherit = "odin.insurance.class"

    engine = fields.Selection(
        selection_add=[("motor", "The motor quote screen")], ondelete={"motor": "cascade"})

    @api.model
    def _odin_engine_tags(self):
        tags = super()._odin_engine_tags()
        tags["motor"] = "odin_motor_quote"
        return tags
