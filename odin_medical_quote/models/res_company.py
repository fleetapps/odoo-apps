from markupsafe import Markup

from odoo import _, fields, models
from odoo.tools import is_html_empty


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


    def _odin_payment_html(self):
        """How to pay, as printed on quotations and invoices: the company's
        own wording, otherwise its bank accounts."""
        self.ensure_one()
        if not is_html_empty(self.odin_quote_payment_note):
            return self.odin_quote_payment_note
        lines = []
        for account in self.partner_id.bank_ids:
            lines.append(Markup("<b>%s</b>") % (account.bank_id.name or _("Bank account")))
            lines.append(_("A/C name: %s", account.acc_holder_name or self.name))
            lines.append(_("A/C no: %s", account.acc_number))
            if account.bank_id.bic:
                lines.append(_("SWIFT: %s", account.bank_id.bic))
        return Markup("<br/>").join(lines) if lines else False


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_quote_payment_note = fields.Html(
        related="company_id.odin_quote_payment_note", readonly=False)
    odin_quote_handler_id = fields.Many2one(
        related="company_id.odin_quote_handler_id", readonly=False)
