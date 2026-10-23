from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_quote_payment_note = fields.Html(
        string="Payment details on medical quotations",
        help="Printed in the Payment options box of a medical quotation: bank "
        "account, M-Pesa paybill and how to quote the reference. Left empty, "
        "the company's bank accounts are printed.",
    )
    odin_quote_handler_id = fields.Many2one(
        "res.users", string="Hand accepted quotes to",
        domain=[("share", "=", False)],
        help="Who places the cover with the insurer once a client accepts a "
        "medical quote: they get the Place cover to-do. Left empty, the "
        "salesperson does.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_quote_payment_note = fields.Html(
        related="company_id.odin_quote_payment_note", readonly=False)
    odin_quote_handler_id = fields.Many2one(
        related="company_id.odin_quote_handler_id", readonly=False)
