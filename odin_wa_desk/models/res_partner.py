from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    odin_wa_junk = fields.Boolean(
        "WhatsApp Junk", copy=False, index="btree_not_null",
        help="Marked as spam or a wrong number from a WhatsApp conversation. No lead "
        "is opened for this contact again, however many times it writes.",
    )
