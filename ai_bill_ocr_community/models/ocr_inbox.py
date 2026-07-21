# -*- coding: utf-8 -*-
"""Inbox: one record per received bill document.

Email intake via the mail gateway: this model implements
message_new()/message_update() so a mail.alias (e.g. bills@yourcompany.odoo.com)
creates inbox records with the PDF/image attachments automatically.
Mail gateway pattern per official mixins docs:
https://www.odoo.com/documentation/19.0/developer/reference/backend/mixins.html
"""
import json
from odoo import api, fields, models


class OcrInbox(models.Model):
    _name = "ai.bill.inbox"
    _description = "AI Bill Inbox"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(default="New", copy=False)
    state = fields.Selection(
        [("new", "New"), ("extracting", "Extracting"),
         ("review", "Needs Review"), ("ready", "Ready"),
         ("done", "Bill Created"), ("failed", "Failed")],
        default="new", tracking=True, index=True)
    attachment_id = fields.Many2one("ir.attachment", string="Document")
    source = fields.Selection([("email", "Email"), ("upload", "Manual Upload")],
                              default="upload")
    company_id = fields.Many2one("res.company",
                                 default=lambda self: self.env.company)
    # ---------------- extraction results ----------------
    raw_json = fields.Text(help="Full LLM response for audit/debug.")
    vendor_name = fields.Char()
    vendor_vat = fields.Char()
    partner_id = fields.Many2one("res.partner", string="Matched Vendor")
    partner_confidence = fields.Float()
    invoice_ref = fields.Char()
    invoice_date = fields.Date()
    due_date = fields.Date()
    currency_code = fields.Char()
    amount_untaxed = fields.Monetary(currency_field="currency_id")
    amount_tax = fields.Monetary(currency_field="currency_id")
    amount_total = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one("res.currency",
                                  default=lambda self: self.env.company.currency_id)
    overall_confidence = fields.Float(
        help="0-1. Below the review threshold -> Needs Review state.")
    line_ids = fields.One2many("ai.bill.inbox.line", "inbox_id")
    move_id = fields.Many2one("account.move", readonly=True, copy=False)

    # ------------------------------------------------------- email intake
    @api.model
    def message_new(self, msg_dict, custom_values=None):
        vals = dict(custom_values or {}, source="email",
                    name=msg_dict.get("subject") or "Email Bill")
        record = super().message_new(msg_dict, vals)
        return record

    def _message_post_after_hook(self, message, msg_vals):
        res = super()._message_post_after_hook(message, msg_vals)
        for rec in self.filtered(lambda r: not r.attachment_id):
            att = message.attachment_ids.filtered(
                lambda a: a.mimetype in ("application/pdf", "image/png",
                                         "image/jpeg"))[:1]
            if att:
                rec.attachment_id = att.id
                rec.with_delay_or_direct_extract()
        return res

    def with_delay_or_direct_extract(self):
        """Extraction is triggered by cron for resilience; direct call kept for
        the Extract Now button."""
        self.write({"state": "new"})

    # ------------------------------------------------------- pipeline
    @api.model
    def cron_extract(self, batch=10):
        for rec in self.search([("state", "=", "new"),
                                ("attachment_id", "!=", False)], limit=batch):
            rec._extract()

    def _extract(self):
        self.ensure_one()
        self.state = "extracting"
        try:
            data = self.env["ai.bill.llm"].extract_bill(self.attachment_id)
            self._apply_extraction(data)
        except Exception as e:  # noqa: BLE001
            self.write({"state": "failed"})
            self.message_post(body=f"Extraction failed: {e}")

    def _apply_extraction(self, data):
        self.ensure_one()
        threshold = float(self.env["ir.config_parameter"].sudo().get_param(
            "ai_bill_ocr_community.review_threshold", "0.85"))
        fieldsmap = data.get("fields", {})
        currency = self.env["res.currency"].search(
            [("name", "=", (fieldsmap.get("currency") or {}).get("value"))], limit=1)
        partner, p_conf = self.env["ai.bill.matcher"].match_partner(
            (fieldsmap.get("vendor_name") or {}).get("value"),
            (fieldsmap.get("vendor_vat") or {}).get("value"))
        confs = [f.get("confidence", 0) for f in fieldsmap.values()
                 if isinstance(f, dict)]
        overall = min([p_conf] + confs) if confs else 0.0
        self.write({
            "raw_json": json.dumps(data)[:100000],
            "vendor_name": (fieldsmap.get("vendor_name") or {}).get("value"),
            "vendor_vat": (fieldsmap.get("vendor_vat") or {}).get("value"),
            "partner_id": partner and partner.id,
            "partner_confidence": p_conf,
            "invoice_ref": (fieldsmap.get("invoice_ref") or {}).get("value"),
            "invoice_date": (fieldsmap.get("invoice_date") or {}).get("value"),
            "due_date": (fieldsmap.get("due_date") or {}).get("value"),
            "amount_untaxed": (fieldsmap.get("amount_untaxed") or {}).get("value") or 0,
            "amount_tax": (fieldsmap.get("amount_tax") or {}).get("value") or 0,
            "amount_total": (fieldsmap.get("amount_total") or {}).get("value") or 0,
            "currency_id": currency.id or self.currency_id.id,
            "overall_confidence": overall,
            "state": "ready" if overall >= threshold else "review",
            "line_ids": [(5, 0, 0)] + [
                (0, 0, {"description": l.get("description"),
                        "quantity": l.get("quantity", 1),
                        "price_unit": l.get("price_unit", 0),
                        "confidence": l.get("confidence", 0)})
                for l in data.get("lines", [])],
        })

    # ------------------------------------------------------- bill creation
    def action_create_bill(self):
        for rec in self:
            if not rec.partner_id:
                rec.state = "review"
                continue
            move = self.env["account.move"].create({
                "move_type": "in_invoice",
                "partner_id": rec.partner_id.id,
                "ref": rec.invoice_ref,
                "invoice_date": rec.invoice_date,
                "invoice_date_due": rec.due_date,
                "currency_id": rec.currency_id.id,
                "invoice_line_ids": [
                    (0, 0, {"name": l.description or "/",
                            "quantity": l.quantity,
                            "price_unit": l.price_unit,
                            "product_id": l.product_id.id or False})
                    for l in rec.line_ids],
            })
            rec.attachment_id.copy({"res_model": "account.move",
                                    "res_id": move.id})
            rec.write({"move_id": move.id, "state": "done"})
        # NOTE: bill stays DRAFT - a human posts it. By design.


class OcrInboxLine(models.Model):
    _name = "ai.bill.inbox.line"
    _description = "Extracted Bill Line"

    inbox_id = fields.Many2one("ai.bill.inbox", required=True,
                               ondelete="cascade")
    description = fields.Char()
    quantity = fields.Float(default=1)
    price_unit = fields.Float()
    confidence = fields.Float()
    product_id = fields.Many2one("product.product",
                                 help="Optional match; empty = expense line.")
