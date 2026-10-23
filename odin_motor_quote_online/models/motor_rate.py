from odoo import fields, models


class MotorRate(models.Model):
    _inherit = "odin.motor.rate"

    odin_online = fields.Boolean(
        string="Show online", default=True,
        help="Offered on the public motor quote page. Untick a rate to quote it only "
        "through an agent.")
