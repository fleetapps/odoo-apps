from odoo import api, fields, models


class MotorClass(models.Model):
    _inherit = "odin.motor.class"

    odin_online = fields.Boolean(
        string="Show online", default=True,
        help="Offered on the public motor quote page. Untick types the public should "
        "talk to an advisor about (construction plant, prime movers...).")
    online_name = fields.Char(
        string="Name online", translate=True,
        help="What the public sees, e.g. 'Taxi, Uber or matatu'. Empty: the name.")
    online_hint = fields.Char(string="Hint online", translate=True,
                              help="One line under the name, e.g. 'Carries paying passengers'.")
    online_icon = fields.Char(
        string="Icon online", default="fa-car",
        help="A Font Awesome 4 icon: fa-car, fa-taxi, fa-truck, fa-bus, fa-motorcycle.")

    @api.model
    def _odin_set_online(self, values):
        """Install-time defaults for the standard vehicle types: their public
        names, hints and icons, and which the public is offered."""
        for xmlid, vals in values.items():
            record = self.env.ref(xmlid, raise_if_not_found=False)
            if record:
                record.write(vals)
