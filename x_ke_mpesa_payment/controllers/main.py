# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils


class MpesaPaymentController(http.Controller):

    @http.route("/payment/mpesa/stk", type="jsonrpc", auth="public")
    def mpesa_stk(self, reference, access_token, phone, **kwargs):
        """Prompt the customer's phone for the payment the form just created."""
        if not payment_utils.check_access_token(access_token, reference):
            raise ValidationError(_("This payment has expired: reload the page and try again."))
        tx = request.env["payment.transaction"].sudo().search(
            [("reference", "=", reference), ("provider_code", "=", "mpesa")], limit=1)
        if not tx:
            raise ValidationError(_("This payment has expired: reload the page and try again."))
        state = tx._mpesa_send_stk(phone)
        if state == "error":
            raise ValidationError(tx.state_message or _("M-PESA refused the payment."))
        return {"state": state}
