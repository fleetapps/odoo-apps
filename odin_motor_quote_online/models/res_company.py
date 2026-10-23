from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_online_motor_url = fields.Char(
        string="Motor quote page", compute="_compute_odin_online_motor_url")

    @api.depends("company_id")
    def _compute_odin_online_motor_url(self):
        for settings in self:
            settings.odin_online_motor_url = "%s%s" % (
                settings.company_id.get_base_url(),
                self.env["odin.motor.quote.online"]._odin_path())
