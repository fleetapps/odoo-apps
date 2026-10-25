# -*- coding: utf-8 -*-
"""Customer invoices and credit notes, signed by eTIMS; and reverse invoices.

Kenyan only, and only where eTIMS is in use: a move of any other company --
or of a Kenyan company with no eTIMS device -- posts, prints and sends exactly
as it did before this module was installed.

Posting queues; it never signs. KRA or a provider being slow, down or
refusing must not stop an accountant from posting, and a network call inside
the posting transaction would be rolled back with it if anything later failed,
leaving KRA with a sale the books do not have.

What is sent is the invoice as Odoo computed it. Each line's VAT-inclusive
amounts come from the line's own VAT tax, converted to shillings at the
invoice's rate, so the receipt KRA signs and the invoice the customer receives
add up to the same thing.

A reverse invoice is the buyer's eTIMS invoice for a purchase from a seller
who cannot issue one, typically a small-scale farmer: ticked on the vendor
bill, it is queued on posting like a sale, and once signed its CU invoice
number is the bill's supplier eTIMS number. Only DigiTax offers it.
"""

import base64
import io
import json
from decimal import Decimal

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..backends import REGISTRY, Operator, Purchase, Sale
from ..backends.base import money
from .ke_etims_code import CODE_CLASS_CREDIT_NOTE_REASON, CODE_CLASS_PAYMENT_TYPE
from .ke_etims_document import INVOICE_KINDS, STATES

#: Seconds: the attempt made just before an invoice is sent.
SEND_TIMEOUT = 4.0

SALE_TYPES = ("out_invoice", "out_receipt")
CREDIT_TYPES = ("out_refund",)
REVERSE_TYPES = ("in_invoice",)
#: KRA tax type of a line with no VAT on it: the seller is not registered.
NON_VAT = "D"
#: KRA payment types (code class 07).
PAYMENT_CASH = "01"
PAYMENT_CREDIT = "02"


def _decimal(value):
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value or 0))


class AccountMove(models.Model):
    _inherit = "account.move"

    ke_etims_document_ids = fields.One2many("ke.etims.document", "move_id",
                                            string="eTIMS Documents")
    ke_etims_document_id = fields.Many2one(
        "ke.etims.document", compute="_compute_ke_etims_document_id",
        string="eTIMS Document", store=True, index="btree_not_null", copy=False)
    ke_etims_state = fields.Selection(
        [("not_sent", "Not Sent")] + STATES, string="eTIMS Status",
        compute="_compute_ke_etims_state", store=True, index=True, copy=False)
    ke_etims_applicable = fields.Boolean(compute="_compute_ke_etims_applicable")
    ke_etims_reason_code_id = fields.Many2one(
        "ke.etims.code", string="KRA Credit Note Reason", copy=False,
        domain=[("code_class", "=", CODE_CLASS_CREDIT_NOTE_REASON)])
    ke_etims_payment_type_id = fields.Many2one(
        "ke.etims.code", string="eTIMS Payment Type",
        domain=[("code_class", "=", CODE_CLASS_PAYMENT_TYPE)],
        compute="_compute_ke_etims_payment_type_id", store=True, readonly=False)

    ke_etims_kra_invoice_no = fields.Integer(
        related="ke_etims_document_id.kra_invoice_no", string="KRA Invoice No.")
    ke_etims_receipt_no = fields.Integer(related="ke_etims_document_id.receipt_no")
    ke_etims_total_receipt_no = fields.Integer(
        related="ke_etims_document_id.total_receipt_no")
    ke_etims_internal_data = fields.Char(related="ke_etims_document_id.internal_data")
    ke_etims_signature = fields.Char(related="ke_etims_document_id.signature")
    ke_etims_scu_id = fields.Char(related="ke_etims_document_id.scu_id")
    ke_etims_signed_at = fields.Datetime(related="ke_etims_document_id.signed_at")
    ke_etims_qr_url = fields.Char(related="ke_etims_document_id.qr_url")
    ke_etims_last_error = fields.Text(related="ke_etims_document_id.last_error")
    ke_etims_is_demo = fields.Boolean(compute="_compute_ke_etims_is_demo")
    ke_etims_qr_image = fields.Binary(compute="_compute_ke_etims_qr_image",
                                      string="eTIMS QR Code")
    ke_etims_cu_invoice = fields.Char(
        compute="_compute_ke_etims_cu_invoice", string="CU Invoice No.",
        help="KRA's number for the signed sale: SCU ID / receipt number.")
    ke_etims_reverse_invoice = fields.Boolean(
        string="eTIMS Reverse Invoice", copy=False, tracking=True,
        help="We issue the eTIMS invoice for this purchase, because the seller "
             "cannot: a small-scale farmer, for instance. Signed on posting.")
    ke_etims_can_reverse_invoice = fields.Boolean(
        compute="_compute_ke_etims_can_reverse_invoice")
    ke_etims_supplier_invoice_ref = fields.Char(
        string="Supplier eTIMS Invoice No.", copy=False, tracking=True,
        help="The CU invoice number on the supplier's eTIMS invoice. From the "
             "2026 tax year, an expense without one is not deductible.")

    # ------------------------------------------------------------ computes

    @api.depends("ke_etims_document_ids.state")
    def _compute_ke_etims_document_id(self):
        for move in self:
            documents = move.sudo().ke_etims_document_ids.filtered(
                lambda d: d.state != "cancelled" and d.kind in INVOICE_KINDS)
            move.ke_etims_document_id = documents.sorted("id")[-1:] if documents else documents

    @api.depends("ke_etims_document_id.signed_environment",
                 "ke_etims_document_id.device_id.environment")
    def _compute_ke_etims_is_demo(self):
        for move in self:
            document = move.sudo().ke_etims_document_id
            move.ke_etims_is_demo = bool(document) and (
                document.signed_environment or document.device_id.environment) == "demo"

    @api.depends("ke_etims_document_ids.state", "state", "country_code", "move_type",
                 "ke_etims_reverse_invoice")
    def _compute_ke_etims_state(self):
        for move in self:
            if move.country_code != "KE" or not move._ke_etims_is_etims_type():
                move.ke_etims_state = False
                continue
            documents = move.sudo().ke_etims_document_ids.filtered(
                lambda d: d.state != "cancelled" and d.kind in INVOICE_KINDS)
            if documents:
                move.ke_etims_state = documents.sorted("id")[-1].state
            elif move.state == "posted" and move.company_id._ke_etims_enabled():
                move.ke_etims_state = "not_sent"
            else:
                move.ke_etims_state = False

    @api.depends("country_code", "move_type", "company_id", "ke_etims_reverse_invoice")
    def _compute_ke_etims_applicable(self):
        for move in self:
            move.ke_etims_applicable = (
                move.country_code == "KE"
                and move._ke_etims_is_etims_type()
                and move.company_id._ke_etims_enabled())

    def _ke_etims_is_etims_type(self):
        """A customer invoice or credit note, or a bill we invoice ourselves."""
        self.ensure_one()
        return self.move_type in SALE_TYPES + CREDIT_TYPES or (
            self.move_type in REVERSE_TYPES and self.ke_etims_reverse_invoice)

    @api.depends("country_code", "move_type", "company_id")
    def _compute_ke_etims_can_reverse_invoice(self):
        for move in self:
            device = move.country_code == "KE" and move.move_type in REVERSE_TYPES \
                and move.company_id._ke_etims_device()
            cls = device and REGISTRY.get(device.sudo().backend)
            move.ke_etims_can_reverse_invoice = bool(
                cls and cls.supports("sign_reverse_invoice"))

    @api.depends("company_id", "move_type")
    def _compute_ke_etims_payment_type_id(self):
        for move in self:
            if not move.ke_etims_payment_type_id and move.country_code == "KE":
                move.ke_etims_payment_type_id = self.env["ke.etims.code"]._ke_etims_find(
                    CODE_CLASS_PAYMENT_TYPE, move._ke_etims_default_payment_type())

    def _ke_etims_default_payment_type(self):
        """The company's default, else what the document is: a sales receipt is
        paid there and then (cash), an invoice is paid later (credit)."""
        self.ensure_one()
        return self.company_id.ke_etims_default_payment_type_id.code or (
            PAYMENT_CASH if self.move_type == "out_receipt" else PAYMENT_CREDIT)

    @api.depends("ke_etims_document_id.state", "ke_etims_document_id.scu_id",
                 "ke_etims_document_id.receipt_no")
    def _compute_ke_etims_cu_invoice(self):
        for move in self:
            document = move.sudo().ke_etims_document_id
            move.ke_etims_cu_invoice = "%s/%s" % (document.scu_id, document.receipt_no) \
                if document.state == "sent" and document.scu_id else False

    @api.depends("ke_etims_qr_url")
    def _compute_ke_etims_qr_image(self):
        for move in self:
            move.ke_etims_qr_image = move._ke_etims_qr_png(move.ke_etims_qr_url)

    @api.model
    def _ke_etims_qr_png(self, value):
        if not value:
            return False
        try:
            import qrcode
        except ImportError:  # pragma: no cover - a manifest dependency
            return False
        image = qrcode.make(value, box_size=4, border=1)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue())

    def _ke_etims_qr_data_uri(self):
        self.ensure_one()
        png = self._ke_etims_qr_png(self.ke_etims_qr_url)
        return "data:image/png;base64,%s" % png.decode() if png else False

    def _ke_etims_report_values(self):
        """What the invoice PDF prints, laid out the way the TIS specification
        (6.21 to 6.23) lays a receipt's SCU information out."""
        self.ensure_one()
        document = self.sudo().ke_etims_document_id
        values = {"signed": False, "demo": False, "unsigned": False}
        if not self.ke_etims_applicable or self.state != "posted":
            return values
        values["demo"] = self.ke_etims_is_demo
        if not document or document.state != "sent":
            values["unsigned"] = True
            return values

        def dashed(text):
            text = (text or "").replace("-", "")
            return "-".join(text[i:i + 4] for i in range(0, len(text), 4))

        def local(moment):
            return fields.Datetime.context_timestamp(
                self.with_context(tz="Africa/Nairobi"), moment) if moment else None

        signed = local(document.signed_at)
        confirmed = local(document.confirmed_at)
        bands = []
        totals = json.loads(document.totals_json) if document.totals_json else {}
        codes = {code.code: code for code in self.env["ke.etims.code"].sudo().search(
            [("code_class", "=", "04")])}
        for letter in ("A", "B", "C", "D", "E"):
            row = totals.get(letter) or {}
            taxable = float(row.get("taxable") or 0.0)
            rate = float(row.get("rate") or (codes[letter].rate if letter in codes else 0.0))
            # 6.21-6.22: a rate above zero is always printed; a zero rate only
            # when something was sold at it.
            if rate > 0 or taxable:
                bands.append({
                    "label": codes[letter].name if letter in codes else letter,
                    "taxable": taxable,
                    "tax": float(row.get("tax") or 0.0),
                })
        original = document.original_document_id
        values.update({
            "signed": True,
            "date": signed.strftime("%d/%m/%Y") if signed else "",
            "time": signed.strftime("%H:%M:%S") if signed else "",
            "cu_id": document.scu_id or "",
            "cu_invoice_no": "%s/%s" % (document.scu_id or "", document.receipt_no or ""),
            "counter": "%s/%s %s" % (document.receipt_no or "", document.total_receipt_no or "",
                                     document.receipt_label),
            "internal_data": dashed(document.internal_data),
            "signature": dashed(document.signature),
            "qr": self._ke_etims_qr_data_uri(),
            "qr_url": document.qr_url,
            "kra_invoice_no": document.kra_invoice_no,
            "tis_date": confirmed.strftime("%d/%m/%Y") if confirmed else "",
            "tis_time": confirmed.strftime("%H:%M:%S") if confirmed else "",
            "original_cu_invoice_no": "%s/%s" % (original.scu_id or "", original.receipt_no or "")
            if original else "",
            "bands": bands,
        })
        return values

    # ----------------------------------------------------------- lifecycle

    def _post(self, soft=True):
        posted = super()._post(soft)
        to_queue = posted.filtered(lambda m: m._ke_etims_should_queue())
        if to_queue:
            to_queue._ke_etims_enqueue()
        return posted

    def _ke_etims_should_queue(self):
        self.ensure_one()
        if self.country_code != "KE" or not self.company_id._ke_etims_enabled():
            return False
        # A reverse invoice was asked for on the bill itself.
        if self.move_type in REVERSE_TYPES:
            return self.ke_etims_reverse_invoice
        return self.move_type in SALE_TYPES + CREDIT_TYPES and self.company_id.ke_etims_auto_send

    def _ke_etims_enqueue(self):
        Document = self.env["ke.etims.document"].sudo()
        documents = Document
        for move in self:
            if move.move_type in CREDIT_TYPES:
                original = move.reversed_entry_id.sudo().ke_etims_document_ids.filtered(
                    lambda d: d.kind == "sale" and d.state != "cancelled")[-1:]
                documents |= Document._ke_etims_enqueue(
                    move, "credit_note", original_document_id=original.id,
                    reason_code=move.ke_etims_reason_code_id.code or False)
            elif move.move_type in REVERSE_TYPES:
                documents |= Document._ke_etims_enqueue(move, "reverse_invoice")
            else:
                documents |= Document._ke_etims_enqueue(move, "sale")
        return documents

    def button_draft(self):
        for move in self.filtered(lambda m: m.country_code == "KE"):
            documents = move.sudo().ke_etims_document_ids.filtered(
                lambda d: d.state != "cancelled")
            if documents.filtered(lambda d: d.state == "sent"):
                raise UserError(_(
                    "%s has been signed by eTIMS and cannot be reset to draft. "
                    "Correct it with a credit note.", move.display_name))
            if documents.filtered(lambda d: d.kra_invoice_no or d.state in ("sending", "pending")):
                raise UserError(_(
                    "%s is being signed by eTIMS (it holds KRA invoice number %s). "
                    "Wait for the answer, or have an eTIMS manager release the "
                    "number, before resetting it.", move.display_name,
                    documents[:1].kra_invoice_no or "-"))
        result = super().button_draft()
        self.sudo().ke_etims_document_ids.filtered(
            lambda d: d.state in ("to_send", "failed") and not d.kra_invoice_no
        ).write({"state": "cancelled"})
        return result

    @api.depends("ke_etims_document_ids.state")
    def _compute_show_reset_to_draft_button(self):
        super()._compute_show_reset_to_draft_button()
        for move in self:
            if move.country_code == "KE" and move.sudo().ke_etims_document_ids.filtered(
                    lambda d: d.state == "sent"):
                move.show_reset_to_draft_button = False

    # -------------------------------------------------------------- actions

    def action_ke_etims_send(self):
        moves = self.filtered(lambda m: m.ke_etims_applicable and m.state == "posted")
        if not moves:
            raise UserError(_("Only posted Kenyan customer invoices, credit notes and "
                              "reverse invoices of a company using eTIMS can be sent."))
        documents = moves._ke_etims_enqueue()
        documents.action_send_now()
        failed = documents.filtered(lambda d: d.state == "failed")
        if len(moves) == 1 and failed:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"title": _("eTIMS"), "type": "danger", "sticky": True,
                           "message": failed[0].last_error or _("Not signed."),
                           "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
            }
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_ke_etims_open_document(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "ke.etims.document",
            "res_id": self.ke_etims_document_id.id,
            "view_mode": "form",
        }

    def _ke_etims_sign_now(self, timeout=SEND_TIMEOUT):
        """One quick attempt for whatever is not signed yet, e.g. before
        emailing. Whatever is still unsigned afterwards stays queued."""
        moves = self.filtered(lambda m: m.ke_etims_applicable and m.state == "posted"
                              and m.ke_etims_state not in ("sent", "pending"))
        if not moves:
            return
        documents = moves._ke_etims_enqueue().filtered(lambda d: d.state == "to_send")
        for document in documents:
            document._ke_etims_process_one(timeout=timeout)

    def action_invoice_sent(self):
        self._ke_etims_sign_now()
        return super().action_invoice_sent()

    # ------------------------------------------------------------- alerts

    @api.depends("partner_id", "ke_etims_reason_code_id", "reversed_entry_id",
                 "ke_etims_reverse_invoice", "invoice_cash_rounding_id",
                 "invoice_line_ids.product_id", "invoice_line_ids.tax_ids",
                 "invoice_line_ids.price_unit", "invoice_line_ids.quantity",
                 "invoice_line_ids.discount")
    def _compute_alerts(self):
        super()._compute_alerts()

    def _get_alerts(self):
        alerts = super()._get_alerts()
        if self.state == "draft" and self.ke_etims_applicable:
            for index, problem in enumerate(self._ke_etims_preflight()[:6]):
                alerts["ke_etims_preflight_%s" % index] = {
                    "level": "warning",
                    "message": _("eTIMS will not accept this as it is. %s", problem),
                }
            if self.invoice_cash_rounding_id.strategy == "biggest_tax":
                alerts["ke_etims_rounding"] = {
                    "level": "warning",
                    "message": _("This cash rounding changes the VAT amount, but KRA works "
                                 "VAT out from the lines, so the two will differ. Use a "
                                 "cash rounding that adds a rounding line instead."),
                }
            return alerts
        if self.state != "posted" or not self.ke_etims_applicable:
            return alerts
        state = self.ke_etims_state
        if state == "failed":
            alerts["ke_etims_failed"] = {
                "level": "warning",
                "message": _("eTIMS could not sign this document: %s",
                             self.ke_etims_last_error or _("unknown error")),
                "action_text": _("Send to eTIMS"),
                "action_call": ("account.move", "action_ke_etims_send", self.ids),
            }
        elif state in ("not_sent", "to_send", "sending", "pending"):
            alerts["ke_etims_unsigned"] = {
                "level": "info",
                "message": _("Not signed by eTIMS yet. Until it is, it is not a "
                             "valid tax invoice."),
                "action_text": _("Send to eTIMS"),
                "action_call": ("account.move", "action_ke_etims_send", self.ids),
            }
        return alerts

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        self.ensure_one()
        return self.company_id._ke_etims_device()

    def _ke_etims_product_lines(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(lambda line: line.display_type == "product")

    def _ke_etims_line_tax(self, line):
        """The one VAT band tax of a line. Taxes without a KRA tax type, such
        as withholding, are not part of the receipt."""
        return line.tax_ids.flatten_taxes_hierarchy().filtered("ke_etims_tax_type_id")

    def _ke_etims_validate(self, document):
        """Everything that can be refused before an invoice number is drawn."""
        self.ensure_one()
        errors = []
        device = document.device_id
        if not self.company_id._ke_etims_is_kenyan():
            return [_("%s is not a Kenyan company.", self.company_id.name)]
        if self.state != "posted":
            return [_("%s is not posted.", self.display_name)]
        if not device._ke_etims_tax_rates():
            errors.append(_("KRA's tax types have not been synced on %s yet; "
                            "use \"Sync KRA codes\" on the device.", device.display_name))
        errors += self._ke_etims_check_document(document)
        if self.move_type in CREDIT_TYPES:
            credit_errors, original = self._ke_etims_check_credit(device, document.reason_code)
            errors += credit_errors
            if original and not credit_errors and not document.original_document_id:
                document.original_document_id = original
        return errors

    def _ke_etims_check_credit(self, device, reason_code=None):
        """A credit note names the signed invoice it corrects, and KRA's reason."""
        self.ensure_one()
        errors = []
        original = self.reversed_entry_id.sudo().ke_etims_document_ids.filtered(
            lambda d: d.kind == "sale" and d.state != "cancelled")[-1:]
        if not self.reversed_entry_id:
            errors.append(_("eTIMS needs the invoice this credit note corrects: choose it "
                            "under \"Credits Invoice\" on the eTIMS tab (reset to draft "
                            "first if it is posted), or credit the invoice from its "
                            "\"Credit Note\" button."))
        elif not original or original.state != "sent":
            errors.append(_("%s has not been signed by eTIMS, so it cannot be "
                            "credited yet.", self.reversed_entry_id.display_name))
        elif device and original.device_id != device:
            errors.append(_("The original invoice was signed by another eTIMS "
                            "device (branch %s).", original.device_id.bhf_id))
        if not (reason_code or self.ke_etims_reason_code_id):
            errors.append(_("Choose the KRA credit note reason on the eTIMS tab."))
        return errors, original

    def _ke_etims_preflight(self):
        """What eTIMS would refuse if this draft were posted now, found
        without asking KRA anything, so it can be put right first."""
        self.ensure_one()
        if not self._ke_etims_product_lines():
            return []
        device = self.company_id._ke_etims_device()
        if device.state != "initialised":
            # Not a refusal: the queue holds it until the device is ready.
            return []
        document = self.env["ke.etims.document"].new({"device_id": device.id})
        problems = self._ke_etims_check_document(document, register=False)
        if self.move_type in CREDIT_TYPES:
            problems += self._ke_etims_check_credit(device)[0]
        return problems

    def _ke_etims_check_document(self, document, register=True):
        """The checks that need no answer from KRA: the reverse invoice, the
        PIN, and every line, with the negative ones folded."""
        reverse = self.move_type in REVERSE_TYPES
        errors = self._ke_etims_validate_reverse(document) if reverse else []
        errors += document._ke_etims_check_pin(
            self.commercial_partner_id.vat, whose=_("seller") if reverse else None)
        errors += document._ke_etims_check_lines(
            self._ke_etims_raw_lines(), currency=self.currency_id, partner=self.partner_id,
            is_refund=self.move_type in CREDIT_TYPES, untaxed_type=self._ke_etims_untaxed_type(),
            register=register,
            refuse_vat=not reverse and not self.company_id.ke_etims_vat_registered)
        return errors

    def _ke_etims_raw_lines(self):
        # A line without a product (typed in, or a down payment) goes as the
        # sundry item, under the line's own description.
        sundry = self.env.ref("x_ke_etims.product_sundry", raise_if_not_found=False)
        return [{
            "product": line.product_id or sundry or line.product_id,
            "taxes": self._ke_etims_line_tax(line),
            "price_unit": line.price_unit,
            "quantity": line.quantity,
            "discount": line.discount,
            "name": line.name or line.product_id.display_name or _("A line"),
        } for line in self._ke_etims_product_lines()]

    def _ke_etims_untaxed_type(self):
        """What a line without VAT goes to KRA as: Non-VAT for a reverse
        invoice, and for every line of a seller not registered for VAT;
        otherwise nothing, and such a line is refused."""
        self.ensure_one()
        if self.move_type in REVERSE_TYPES or not self.company_id.ke_etims_vat_registered:
            return NON_VAT
        return None

    def _ke_etims_validate_reverse(self, document):
        device = document.device_id
        if not REGISTRY[device.backend].supports("sign_reverse_invoice"):
            return [_("%(device)s cannot issue reverse invoices: its backend does not "
                      "offer them (DigiTax does). Untick \"eTIMS Reverse Invoice\" and "
                      "get the seller's own eTIMS invoice instead.",
                      device=device.display_name)]
        seller = self.commercial_partner_id
        if seller.country_id and seller.country_id.code != "KE":
            return [_("A reverse invoice is for a seller in Kenya; %s is not. An "
                      "import is backed by its customs entry.", seller.display_name)]
        return []

    def _ke_etims_lines(self, document):
        """The product lines in shillings, VAT included, with the negative
        ones folded in as discount."""
        self.ensure_one()
        rate = _decimal(self.invoice_currency_rate or 1.0)
        company_currency = self.company_id.currency_id

        def to_kes(amount):
            amount = _decimal(amount)
            if self.currency_id != company_currency and rate:
                amount = amount / rate
            return money(amount)

        return document._ke_etims_build_lines(
            self._ke_etims_raw_lines(), currency=self.currency_id, partner=self.partner_id,
            is_refund=self.move_type in CREDIT_TYPES, to_company=to_kes,
            untaxed_type=self._ke_etims_untaxed_type())

    def _ke_etims_prepare(self, document):
        """The invoice as a ``Sale``, in shillings, VAT included; a reverse
        invoice as the ``Purchase`` we invoice ourselves."""
        self.ensure_one()
        device = document.device_id
        credit = self.move_type in CREDIT_TYPES
        lines = self._ke_etims_lines(document)
        partner = self.commercial_partner_id
        if self.move_type in REVERSE_TYPES:
            return Purchase(
                invoice_no=document.kra_invoice_no,
                lines=lines,
                purchase_date=self.invoice_date or fields.Date.context_today(self),
                tax_rates=device._ke_etims_tax_rates(),
                operator=self._ke_etims_operator(device),
                payment_type=self.ke_etims_payment_type_id.code or PAYMENT_CASH,
                supplier_pin=(partner.vat or "").strip().upper() or None,
                supplier_name=partner.name,
                confirmed_at=document.confirmed_at,
                remark=self.ref or self.name,
            )
        original = document.original_document_id
        return Sale(
            invoice_no=document.kra_invoice_no,
            trader_invoice_no=self.name,
            lines=lines,
            confirmed_at=document.confirmed_at,
            sale_date=self.invoice_date or fields.Date.context_today(self),
            tax_rates=device._ke_etims_tax_rates(),
            operator=self._ke_etims_operator(device),
            receipt_type="R" if credit else "S",
            customer_pin=(partner.vat or "").strip().upper() or None,
            customer_name=partner.name,
            customer_phone=partner.phone or self.partner_id.phone or None,
            payment_type=self.ke_etims_payment_type_id.code or self._ke_etims_default_payment_type(),
            original_invoice_no=original.kra_invoice_no if credit else None,
            original_provider_ref=original.provider_ref if credit else None,
            reason_code=(document.reason_code or self.ke_etims_reason_code_id.code)
            if credit else None,
            refunded_at=document.confirmed_at if credit else None,
            idempotency_key=document.idempotency_key,
            callback_url=device.sudo().callback_url or None,
            trade_name=self.company_id.name,
        )

    def _ke_etims_operator(self, device):
        user = self.invoice_user_id or self.create_uid or self.env.user
        return Operator(id=(user.login or str(user.id))[:20], name=(user.name or "Odoo")[:60])

    def _ke_etims_on_signed(self, document):
        self.ensure_one()
        if document.kind == "reverse_invoice" and document.scu_id:
            # Our own eTIMS invoice backs the expense, as a supplier's would.
            self.ke_etims_supplier_invoice_ref = "%s/%s" % (
                document.scu_id, document.receipt_no or document.kra_invoice_no)
        self.message_post(body=_(
            "Signed by eTIMS: KRA invoice %(number)s, receipt %(receipt)s/%(total)s %(label)s, "
            "SCU %(scu)s.",
            number=document.kra_invoice_no, receipt=document.receipt_no or "-",
            total=document.total_receipt_no or "-", label=document.receipt_label,
            scu=document.scu_id or "-"))
        # A PDF made before the signature says it is not a tax invoice. Detach
        # it, as resetting to draft would, so the next print carries the
        # signature and QR code instead of serving the stale copy.
        if self.invoice_pdf_report_id:
            self.sudo()._detach_attachments()
        # The activity for an earlier failure is resolved.
        self.activity_ids.filtered(
            lambda a: a.summary == self._ke_etims_activity_summary()).action_feedback(
            feedback=_("Signed by eTIMS."))

    def _ke_etims_activity_summary(self):
        return _("eTIMS could not sign this document")

    def _ke_etims_on_failed(self, document):
        self.ensure_one()
        self.message_post(body=_("eTIMS could not sign %(name)s: %(error)s",
                                 name=self.display_name, error=document.last_error))
        summary = self._ke_etims_activity_summary()
        if self.activity_ids.filtered(lambda a: a.summary == summary):
            return
        user = self.invoice_user_id or document.device_id.responsible_user_id
        if not user or user._is_public() or user._is_superuser():
            return
        self.sudo().activity_schedule(
            "mail.mail_activity_data_warning", summary=summary,
            note=document.last_error, user_id=user.id)

    # ---------------------------------------------------------------- views

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type="form", **options):
        key = super()._get_view_cache_key(view_id, view_type, **options)
        return key + (self.env["res.company"]._ke_etims_any_kenyan(self.env.companies),)

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        """Take the eTIMS column and filters out for someone who works in no
        Kenyan company, so a non-Kenyan user sees the invoice list unchanged."""
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type in ("list", "search") \
                and not self.env["res.company"]._ke_etims_any_kenyan(self.env.companies):
            for node in arch.xpath(
                    "//field[@name='ke_etims_state'] | //filter[starts-with(@name, 'ke_etims')]"):
                node.getparent().remove(node)
        return arch, view
