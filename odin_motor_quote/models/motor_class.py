from odoo import fields, models


class MotorClass(models.Model):
    """A kind of vehicle as the rate cards price it: private car, PSV taxi,
    commercial, motorcycle... The questions the quote asks depend on it."""

    _name = "odin.motor.class"
    _description = "Vehicle type (motor quotes)"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    ask_seats = fields.Boolean(
        string="Ask for passengers",
        help="Passenger legal liability, and some third-party rates, are priced "
        "per passenger.")
    ask_tonnage = fields.Boolean(
        string="Ask for tonnage",
        help="Third-party rates for goods vehicles go by carrying capacity.")
    rate_ids = fields.One2many("odin.motor.rate", "class_id", string="Rates")
