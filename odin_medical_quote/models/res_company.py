from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_quote_payment_note = fields.Html(
        string="Payment details on medical quotations",
        help="Printed in the Payment options box of a medical quotation: bank "
        "account, M-Pesa paybill and how to quote the reference. Left empty, "
        "the company's bank accounts are printed.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_quote_payment_note = fields.Html(
        related="company_id.odin_quote_payment_note", readonly=False)
