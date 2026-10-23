from odoo import api, models


class MedicalQuote(models.AbstractModel):
    _inherit = "odin.medical.quote"

    @api.model
    def _odin_templates(self, cover):
        """Online, only the plans the broker shows the public."""
        templates = super()._odin_templates(cover)
        if self.env.context.get("odin_online"):
            templates = templates.filtered("odin_online")
        return templates
