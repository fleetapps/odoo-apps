# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""A portal or website payment by STK Push.

The customer types their number, Odoo prompts that phone, and the payment
waits (pending) for M-PESA. The STK callback, through the receipt log,
settles it: done, cancelled or failed. Odoo then creates the payment as for
any provider, and the receipt's statement line is reconciled with it.
"""

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.x_ke_mpesa.lib import daraja

# The customer cancelled the prompt on their phone.
CANCELLED = {"1032"}


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    mpesa_transaction_id = fields.Many2one(
        "mpesa.transaction", string="M-Pesa request", readonly=True, copy=False)

    def _get_specific_processing_values(self, processing_values):
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != "mpesa":
            return res
        # The prompt route only acts for the browser that started this payment.
        return {"mpesa_access_token": payment_utils.generate_access_token(
            self.reference, env=self.env)}

    def _mpesa_send_stk(self, phone):
        """Prompt ``phone`` for this payment. Returns the transaction's state."""
        self.ensure_one()
        if self.provider_code != "mpesa":
            raise ValidationError(_("%s is not an M-Pesa payment.", self.reference))
        if self.state not in ("draft", "pending"):
            raise ValidationError(_("Payment %s is already processed.", self.reference))
        shortcode = self.provider_id.mpesa_shortcode_id
        if not shortcode:
            raise ValidationError(_("M-Pesa payments are not set up yet."))
        invoice = self.invoice_ids[:1]
        request = self.env["mpesa.transaction"].sudo()._stk_request(
            shortcode, self.amount, phone,
            reference=invoice.name or self.reference,
            description=self.company_id.name,
            origin=self,
            idempotency_key=f"payment:{self.reference}",
            partner=self.partner_id.commercial_partner_id,
            # Nobody in the back office is waiting for this one.
            extra_vals={"user_id": False},
        )
        self.mpesa_transaction_id = request
        if request.state in ("confirmed", "failed"):
            self._mpesa_update()
        elif request.checkout_request_id:
            self.provider_reference = request.checkout_request_id
            self._set_pending(state_message=_(
                "We sent a payment prompt to %s. Enter your M-PESA PIN on the phone to pay; "
                "this page updates by itself.", request.msisdn_masked))
        return self.state

    def _mpesa_update(self):
        """Follow the M-Pesa request: called when it settles."""
        for tx in self.filtered(lambda t: t.provider_code == "mpesa" and t.mpesa_transaction_id):
            if tx.state in ("draft", "pending"):
                tx._process("mpesa", {"reference": tx.reference})

    def _extract_amount_data(self, payment_data):
        if self.provider_code != "mpesa":
            return super()._extract_amount_data(payment_data)
        request = self.mpesa_transaction_id
        if request.state != "confirmed":
            return None  # nothing was paid: no amount to check
        amount = request.amount
        # M-PESA takes whole shillings: KES 1,000.40 is paid with 1,000.
        if daraja.whole_shillings(self.amount) == daraja.whole_shillings(amount):
            amount = self.amount
        return {"amount": amount, "currency_code": request.currency_id.name}

    def _apply_updates(self, payment_data):
        if self.provider_code != "mpesa":
            return super()._apply_updates(payment_data)
        request = self.mpesa_transaction_id
        if request.state == "confirmed":
            if request.receipt:
                self.provider_reference = request.receipt
            self._set_done()
            # Create the payment soon, even if the customer has left the page.
            self.env.ref("payment.cron_post_process_payment_tx")._trigger()
        elif request.state == "failed":
            if request.result_code in CANCELLED:
                self._set_canceled(state_message=request.cashier_message)
            else:
                self._set_error(request.cashier_message or _("M-PESA refused the payment."))

    def _post_process(self):
        super()._post_process()
        # Now that the payment exists, its receipt's statement line can meet it.
        requests = self.filtered(
            lambda t: t.provider_code == "mpesa" and t.state == "done" and t.payment_id
        ).mpesa_transaction_id.filtered(lambda r: r.state == "confirmed")
        requests._auto_match()
