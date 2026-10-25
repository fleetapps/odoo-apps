# -*- coding: utf-8 -*-
"""Sending an invoice: one quick try at eTIMS first, then a warning if it failed.

An invoice emailed before it is signed reaches the customer without its KRA
signature and QR code, which is not a valid tax invoice. So when invoices are
about to be sent, each unsigned Kenyan one gets a single synchronous attempt
of about four seconds. Whatever is still unsigned is flagged in the send
dialog; sending is not blocked, because the accountant may have a reason, and
the queue keeps trying regardless.
"""

from odoo import _, api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _get_alerts(self, moves, moves_data):
        alerts = super()._get_alerts(moves, moves_data)
        unsigned = moves.filtered(
            lambda m: m.ke_etims_applicable and m.state == "posted"
            and m.ke_etims_state != "sent")
        if unsigned:
            alerts["ke_etims_unsigned"] = {
                "level": "warning",
                "message": _(
                    "%s not signed by eTIMS yet: the PDF will not carry the KRA "
                    "signature and QR code, and is not a valid tax invoice until "
                    "it does.", ", ".join(unsigned.mapped("display_name"))),
                "action_text": _("View"),
                "action": unsigned._get_records_action(name=_("Not signed by eTIMS")),
            }
        return alerts

    @api.model
    def _call_web_service_before_invoice_pdf_render(self, invoices_data):
        super()._call_web_service_before_invoice_pdf_render(invoices_data)
        moves = self.env["account.move"].union(*invoices_data.keys()) \
            if invoices_data else self.env["account.move"]
        moves._ke_etims_sign_now()
