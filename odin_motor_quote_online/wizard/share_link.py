from odoo import fields, models


class OnlineQuoteShare(models.TransientModel):
    _inherit = "odin.medical.quote.online.share"

    quote_type = fields.Selection(selection_add=[("motor", "Motor")],
                                  ondelete={"motor": "set default"})
