# -*- coding: utf-8 -*-
"""POS orders as eTIMS sales and credit notes.

A paid order is queued when it is saved, like an invoice is when it is posted,
so nothing at the till ever waits on KRA. The till then asks for the
signature itself (``ke_etims_sign_from_ui``), with a timeout, and prints
whatever it has: the signature, or "eTIMS: pending".

An order that is invoiced is not queued as an order: its invoice is posted and
signed, and the order shows the invoice's receipt. Signing both would declare
the sale twice.
"""

from datetime import datetime, timezone
from decimal import Decimal

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_etims.backends import Operator, Sale
from odoo.addons.x_ke_etims.backends.base import money

#: Seconds the server spends trying to sign while the till waits. The till's
#: own wait (pos.config.ke_etims_sign_timeout) is a little longer.
SIGN_TIMEOUT = 4.0


class PosOrder(models.Model):
    _inherit = "pos.order"

    ke_etims_applicable = fields.Boolean(compute="_compute_ke_etims")
    ke_etims_document_id = fields.Many2one("ke.etims.document", compute="_compute_ke_etims",
                                           string="eTIMS Document")
    ke_etims_state = fields.Selection(
        selection=lambda self: self.env["ke.etims.document"]._fields["state"].selection,
        compute="_compute_ke_etims", string="eTIMS Status")
    ke_etims_is_demo = fields.Boolean(compute="_compute_ke_etims")
    ke_etims_kra_invoice_no = fields.Integer(compute="_compute_ke_etims",
                                             string="KRA Invoice No.")
    ke_etims_receipt_no = fields.Integer(compute="_compute_ke_etims")
    ke_etims_total_receipt_no = fields.Integer(compute="_compute_ke_etims")
    ke_etims_receipt_label = fields.Char(compute="_compute_ke_etims")
    ke_etims_internal_data = fields.Char(compute="_compute_ke_etims")
    ke_etims_signature = fields.Char(compute="_compute_ke_etims")
    ke_etims_scu_id = fields.Char(compute="_compute_ke_etims")
    ke_etims_qr_url = fields.Char(compute="_compute_ke_etims")
    ke_etims_signed_at_display = fields.Char(
        compute="_compute_ke_etims",
        help="When KRA signed it, as the receipt prints it: dd/mm/yyyy hh:mm:ss, Nairobi time.")
    ke_etims_original_cu_invoice = fields.Char(
        compute="_compute_ke_etims",
        help="For a refund, the CU invoice number of the sale it credits.")

    def _ke_etims_documents(self):
        """This order's live document, or its invoice's."""
        self.ensure_one()
        own = self.env["ke.etims.document"].sudo().search([
            ("res_model", "=", "pos.order"), ("res_id", "=", self.id),
            ("state", "!=", "cancelled")], order="id desc", limit=1)
        return own or self.sudo().account_move.ke_etims_document_id

    def _compute_ke_etims(self):
        for order in self:
            document = order._ke_etims_documents() if order.id else self.env["ke.etims.document"]
            order.ke_etims_applicable = bool(order.company_id) and \
                order.company_id._ke_etims_enabled()
            order.ke_etims_document_id = document
            order.ke_etims_state = document.state or False
            order.ke_etims_is_demo = bool(document) and (
                document.signed_environment or document.device_id.environment) == "demo"
            signed = document.state == "sent"
            order.ke_etims_kra_invoice_no = document.kra_invoice_no if signed else 0
            order.ke_etims_receipt_no = document.receipt_no if signed else 0
            order.ke_etims_total_receipt_no = document.total_receipt_no if signed else 0
            order.ke_etims_receipt_label = document.receipt_label if document else False
            order.ke_etims_internal_data = document.internal_data if signed else False
            order.ke_etims_signature = document.signature if signed else False
            order.ke_etims_scu_id = document.scu_id if signed else False
            order.ke_etims_qr_url = document.qr_url if signed else False
            order.ke_etims_signed_at_display = order._ke_etims_nairobi(document.signed_at) \
                if signed else False
            original = document.original_document_id
            order.ke_etims_original_cu_invoice = "%s/%s" % (original.scu_id, original.receipt_no) \
                if original and original.state == "sent" else False

    def _ke_etims_nairobi(self, moment):
        if not moment:
            return False
        local = fields.Datetime.context_timestamp(self.with_context(tz="Africa/Nairobi"), moment)
        return local.strftime("%d/%m/%Y %H:%M:%S")

    # ----------------------------------------------------------- lifecycle

    def _process_saved_order(self, draft):
        order_id = super()._process_saved_order(draft)
        if not draft and self._ke_etims_should_queue():
            self._ke_etims_enqueue()
        return order_id

    def _ke_etims_should_queue(self):
        self.ensure_one()
        return (self.state in ("paid", "done")
                and not self.to_invoice
                and not self.account_move
                and self.company_id._ke_etims_enabled()
                and bool(self.lines))

    def _ke_etims_is_credit(self):
        return bool(self.refunded_order_id) or self.amount_total < 0

    def _ke_etims_enqueue(self):
        Document = self.env["ke.etims.document"].sudo()
        documents = Document
        for order in self:
            if order._ke_etims_is_credit():
                original = order.refunded_order_id._ke_etims_documents() \
                    if order.refunded_order_id else Document
                documents |= Document._ke_etims_enqueue(
                    order, "credit_note", original_document_id=original.id,
                    reason_code=order.config_id.ke_etims_refund_reason_id.code or False)
            else:
                documents |= Document._ke_etims_enqueue(order, "sale")
        return documents

    # ------------------------------------------------------------- the till

    def ke_etims_sign_from_ui(self):
        """Called by the till right after an order is validated, and before a
        reprint of an order still pending: one short attempt, then whatever
        there is. Never raises for an eTIMS failure; the till prints pending."""
        for order in self:
            if not order.company_id._ke_etims_enabled():
                continue
            document = order._ke_etims_documents()
            if not document and order._ke_etims_should_queue():
                document = order._ke_etims_enqueue()
            if document.state == "to_send":
                document._ke_etims_process_one(timeout=SIGN_TIMEOUT)
            elif document.state == "pending":
                document._ke_etims_poll_one(timeout=SIGN_TIMEOUT)
        self.invalidate_recordset()
        return [order._ke_etims_ui_values() for order in self]

    def _ke_etims_ui_values(self):
        self.ensure_one()
        return {
            "id": self.id,
            "ke_etims_applicable": self.ke_etims_applicable,
            "ke_etims_state": self.ke_etims_document_id.state or False,
            "ke_etims_is_demo": self.ke_etims_is_demo,
            "ke_etims_kra_invoice_no": self.ke_etims_kra_invoice_no,
            "ke_etims_receipt_no": self.ke_etims_receipt_no,
            "ke_etims_total_receipt_no": self.ke_etims_total_receipt_no,
            "ke_etims_receipt_label": self.ke_etims_receipt_label,
            "ke_etims_internal_data": self.ke_etims_internal_data,
            "ke_etims_signature": self.ke_etims_signature,
            "ke_etims_scu_id": self.ke_etims_scu_id,
            "ke_etims_qr_url": self.ke_etims_qr_url,
            "ke_etims_signed_at_display": self.ke_etims_signed_at_display,
            "ke_etims_original_cu_invoice": self.ke_etims_original_cu_invoice,
        }

    def action_ke_etims_send(self):
        documents = self.env["ke.etims.document"]
        for order in self:
            if not order.company_id._ke_etims_enabled():
                raise UserError(_("%s does not use eTIMS.", order.company_id.name))
            documents |= order._ke_etims_documents() or order._ke_etims_enqueue()
        documents.action_send_now()
        return True

    def action_ke_etims_open_document(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "ke.etims.document",
            "res_id": self.ke_etims_document_id.id,
            "view_mode": "form",
        }

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        self.ensure_one()
        return self.company_id._ke_etims_device()

    def _ke_etims_lines(self):
        self.ensure_one()
        return self.lines.filtered(lambda line: line.qty)

    @staticmethod
    def _ke_etims_line_tax(line):
        return line.tax_ids_after_fiscal_position.flatten_taxes_hierarchy().filtered(
            "ke_etims_tax_type_id")

    def _ke_etims_validate(self, document):
        self.ensure_one()
        if not self.company_id._ke_etims_is_kenyan():
            return [_("%s is not a Kenyan company.", self.company_id.name)]
        errors = document._ke_etims_check_pin(self.partner_id.commercial_partner_id.vat)
        lines = self._ke_etims_lines()
        if not lines:
            errors.append(_("The order has no lines to send."))
        for line in lines:
            errors += document._ke_etims_check_line(
                line.product_id, self._ke_etims_line_tax(line),
                line.full_product_name or line.product_id.display_name,
                abs(line.qty), line.price_unit)
        if document.kind == "credit_note":
            original = document.original_document_id or (
                self.refunded_order_id._ke_etims_documents() if self.refunded_order_id
                else self.env["ke.etims.document"])
            if not original:
                errors.append(_("A refund sent to eTIMS must credit a signed order; this "
                                "one refunds nothing that was signed."))
            elif original.state != "sent":
                errors.append(_("%s has not been signed by eTIMS yet, so it cannot be "
                                "credited yet.", self.refunded_order_id.display_name))
            elif original.device_id != document.device_id:
                errors.append(_("The original order was signed by another eTIMS device "
                                "(branch %s).", original.device_id.bhf_id))
            elif not document.original_document_id:
                document.original_document_id = original
            if not document.reason_code:
                errors.append(_("Choose the eTIMS refund reason on the point of sale."))
        return errors

    def _ke_etims_payment_type(self):
        """KRA's payment type for the method that paid most of the order."""
        payments = self.payment_ids.filtered(lambda p: not p.is_change)
        if not payments:
            return self.company_id.ke_etims_default_payment_type_id.code or None
        largest = max(payments, key=lambda p: abs(p.amount))
        return largest.payment_method_id.ke_etims_payment_type_id.code \
            or self.company_id.ke_etims_default_payment_type_id.code or None

    def _ke_etims_prepare(self, document):
        self.ensure_one()
        device = document.device_id
        credit = document.kind == "credit_note"
        rate = Decimal(str(self.currency_rate or 1.0))
        same_currency = self.currency_id == self.company_id.currency_id

        def to_kes(amount):
            amount = Decimal(repr(amount)) if isinstance(amount, float) else Decimal(str(amount))
            return money(amount if same_currency or not rate else amount / rate)

        lines = [
            document._ke_etims_build_line(
                seq, line.product_id, self._ke_etims_line_tax(line),
                price_unit=line.price_unit, quantity=abs(line.qty), discount=line.discount,
                name=line.full_product_name, currency=self.currency_id,
                partner=self.partner_id, is_refund=credit, to_company=to_kes)
            for seq, line in enumerate(self._ke_etims_lines(), start=1)
        ]
        partner = self.partner_id.commercial_partner_id
        original = document.original_document_id
        user = self.user_id or self.env.user
        return Sale(
            invoice_no=document.kra_invoice_no,
            trader_invoice_no=self.pos_reference or self.name,
            lines=lines,
            confirmed_at=document.confirmed_at,
            sale_date=(self.date_order or datetime.now(timezone.utc)).date(),
            tax_rates=device._ke_etims_tax_rates(),
            operator=Operator(id=(user.login or str(user.id))[:20], name=(user.name or "POS")[:60]),
            receipt_type="R" if credit else "S",
            customer_pin=(partner.vat or "").strip().upper() or None,
            customer_name=partner.name or None,
            customer_phone=partner.phone or None,
            payment_type=self._ke_etims_payment_type(),
            original_invoice_no=original.kra_invoice_no if credit else None,
            original_provider_ref=original.provider_ref if credit else None,
            reason_code=document.reason_code if credit else None,
            refunded_at=document.confirmed_at if credit else None,
            idempotency_key=document.idempotency_key,
            callback_url=device.sudo().callback_url or None,
            trade_name=self.company_id.name,
        )

    def _ke_etims_on_signed(self, document):
        self.ensure_one()
        self.message_post(body=_(
            "Signed by eTIMS: KRA invoice %(number)s, receipt %(receipt)s/%(total)s %(label)s.",
            number=document.kra_invoice_no, receipt=document.receipt_no or "-",
            total=document.total_receipt_no or "-", label=document.receipt_label))

    def _ke_etims_on_failed(self, document):
        self.ensure_one()
        self.message_post(body=_("eTIMS could not sign %(name)s: %(error)s",
                                 name=self.display_name, error=document.last_error))
        document._ke_etims_schedule_activity()
