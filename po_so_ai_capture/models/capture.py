# -*- coding: utf-8 -*-
"""PO Capture: one record per received customer purchase order document.

The pipeline (each arrow is a state):
  email/upload -> new -> extracting -> matching -> review|ready -> done(SO)
                                                     ^ human fixes here, and
                                                       every fix TEACHES the
                                                       sku-alias memory.

Email intake: mail gateway pattern - this model implements message_new() so a
mail.alias (orders@<domain>) creates capture records from inbound mail with
PDF/image attachments. Mixins reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/mixins.html
ORM reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
"""
import json
from odoo import api, fields, models


class PoCapture(models.Model):
    _name = "po.capture"
    _description = "Captured Customer PO"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", copy=False)
    state = fields.Selection(
        [("new", "New"), ("extracting", "Extracting"),
         ("review", "Needs Review"), ("ready", "Ready"),
         ("done", "SO Created"), ("rejected", "Rejected"),
         ("failed", "Failed")],
        default="new", tracking=True, index=True, group_expand="_expand_states")
    attachment_id = fields.Many2one("ir.attachment", string="PO Document")
    source = fields.Selection([("email", "Email"), ("upload", "Manual Upload")],
                              default="upload")
    email_from = fields.Char(help="Sender - first signal for customer matching.")
    company_id = fields.Many2one("res.company",
                                 default=lambda self: self.env.company)
    # ------------- extraction -------------
    raw_json = fields.Text(help="Full LLM output kept for audit.")
    customer_name = fields.Char(string="Customer (as printed)")
    customer_po_ref = fields.Char(string="Customer PO #")
    po_date = fields.Date()
    requested_delivery = fields.Date()
    currency_code = fields.Char()
    note = fields.Text(string="Special instructions (extracted)")
    # ------------- matching -------------
    partner_id = fields.Many2one("res.partner", string="Matched Customer",
                                 tracking=True)
    partner_confidence = fields.Float()
    line_ids = fields.One2many("po.capture.line", "capture_id")
    matched_pct = fields.Float(compute="_compute_health", store=True,
                               help="Share of lines with a confident product match.")
    overall_confidence = fields.Float(compute="_compute_health", store=True)
    # ------------- output -------------
    sale_order_id = fields.Many2one("sale.order", readonly=True, copy=False)
    duplicate_of_id = fields.Many2one(
        "po.capture", readonly=True,
        help="Same customer + same PO number already captured.")

    @api.model
    def _expand_states(self, states, domain):
        return [s[0] for s in self._fields["state"].selection]

    @api.depends("line_ids.product_id", "line_ids.line_confidence",
                 "partner_confidence")
    def _compute_health(self):
        for rec in self:
            lines = rec.line_ids
            matched = lines.filtered("product_id")
            rec.matched_pct = len(matched) / len(lines) if lines else 0.0
            confs = lines.mapped("line_confidence") + [rec.partner_confidence]
            rec.overall_confidence = min(confs) if confs else 0.0

    # --------------------------------------------------------- email intake
    @api.model
    def message_new(self, msg_dict, custom_values=None):
        vals = dict(custom_values or {}, source="email",
                    email_from=msg_dict.get("email_from"),
                    name=msg_dict.get("subject") or "Email PO")
        return super().message_new(msg_dict, vals)

    def _message_post_after_hook(self, message, msg_vals):
        res = super()._message_post_after_hook(message, msg_vals)
        for rec in self.filtered(lambda r: not r.attachment_id):
            att = message.attachment_ids.filtered(
                lambda a: a.mimetype in ("application/pdf", "image/png",
                                         "image/jpeg"))[:1]
            if att:
                rec.attachment_id = att.id
        return res

    # ------------------------------------------------------------ pipeline
    @api.model
    def cron_extract(self, batch=10):
        for rec in self.search([("state", "=", "new"),
                                ("attachment_id", "!=", False)], limit=batch):
            rec.action_extract()

    def action_extract(self):
        for rec in self:
            rec.state = "extracting"
            try:
                data = self.env["po.capture.llm"].extract_po(rec.attachment_id)
                rec._apply_extraction(data)
                rec._match_all()
            except Exception as e:  # noqa: BLE001
                rec.write({"state": "failed"})
                rec.message_post(body=f"Extraction failed: {e}")

    def _apply_extraction(self, data):
        self.ensure_one()
        f = data.get("fields", {})
        get = lambda k: (f.get(k) or {}).get("value")  # noqa: E731
        self.write({
            "raw_json": json.dumps(data)[:100000],
            "customer_name": get("customer_name"),
            "customer_po_ref": get("po_number"),
            "po_date": get("po_date"),
            "requested_delivery": get("requested_delivery"),
            "currency_code": get("currency"),
            "note": get("notes"),
            "line_ids": [(5, 0, 0)] + [
                (0, 0, {
                    "customer_code": l.get("customer_sku"),
                    "description": l.get("description"),
                    "quantity": l.get("quantity", 1),
                    "price_unit": l.get("price_unit", 0.0),
                    "extract_confidence": l.get("confidence", 0.0),
                }) for l in data.get("lines", [])],
        })

    def _match_all(self):
        self.ensure_one()
        Matcher = self.env["po.capture.matcher"]
        partner, p_conf = Matcher.match_partner(
            self.customer_name, self.email_from)
        self.write({"partner_id": partner.id or False,
                    "partner_confidence": p_conf})
        for line in self.line_ids:
            line.rematch()
        # duplicate detection: same customer + same PO ref
        dup = self.customer_po_ref and self.search([
            ("id", "!=", self.id),
            ("partner_id", "=", self.partner_id.id),
            ("customer_po_ref", "=ilike", self.customer_po_ref),
            ("state", "!=", "rejected")], limit=1)
        threshold = float(self.env["ir.config_parameter"].sudo().get_param(
            "po_so_ai_capture.review_threshold", "0.85"))
        state = "ready"
        if dup:
            self.duplicate_of_id = dup.id
            state = "review"
        elif (not self.partner_id or self.matched_pct < 1.0
              or self.overall_confidence < threshold):
            state = "review"
        self.state = state

    # ------------------------------------------------------------ SO creation
    def action_create_so(self):
        for rec in self:
            if not rec.partner_id or any(not l.product_id for l in rec.line_ids):
                rec.state = "review"
                continue
            # LEARNING STEP: persist every (customer, customer_code -> product)
            # pair so next month's PO from this customer auto-matches.
            rec.line_ids._teach_aliases()
            so = self.env["sale.order"].create({
                "partner_id": rec.partner_id.id,
                "client_order_ref": rec.customer_po_ref,
                "commitment_date": rec.requested_delivery,
                "company_id": rec.company_id.id,
                "note": rec.note or "",
                "origin": f"PO Capture {rec.name}",
                "order_line": [(0, 0, {
                    "product_id": l.product_id.id,
                    "product_uom_qty": l.quantity,
                    "name": l.description or l.product_id.name,
                    # Price policy: keep Odoo pricelist price by default; the
                    # customer's printed price is stored for the clerk to compare.
                }) for l in rec.line_ids],
            })
            rec.attachment_id.copy({"res_model": "sale.order", "res_id": so.id})
            rec.write({"sale_order_id": so.id, "state": "done"})
            so.message_post(
                body=f"Created from customer PO {rec.customer_po_ref or ''} "
                     f"(AI capture, min confidence "
                     f"{rec.overall_confidence:.0%}).")
        # SO stays DRAFT: the human confirms. That is the product's promise -
        # it replaces retyping, not judgement.

    def action_reject(self):
        self.write({"state": "rejected"})
