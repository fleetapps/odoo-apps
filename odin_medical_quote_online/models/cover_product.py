from odoo import api, fields, models


class CoverProduct(models.Model):
    _inherit = "odin.cover.product"

    odin_online = fields.Boolean(
        string="Show on website quote", default=True, tracking=True,
        help="Offered on the public online quote page. Untick to quote it only through "
        "an agent.")

    def _odin_template_values(self):
        values = super()._odin_template_values()
        values["odin_online"] = self.odin_online
        return values

    @api.model
    def _odin_adopt_values(self, template, tables):
        return dict(super()._odin_adopt_values(template, tables), odin_online=template.odin_online)
