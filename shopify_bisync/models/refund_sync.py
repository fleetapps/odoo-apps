# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Refund import (spec A1): ``refunds/create`` webhook -> DRAFT credit note.

The credit note mirrors the Shopify refund exactly (refunded quantities and
amounts, shipping adjustments), references the SO's posted invoice via
``reversed_entry_id`` and is NEVER auto-posted - accounting reviews and
posts it.
"""
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class RefundSync(models.AbstractModel):
    _name = "shopify.bisync.refund.sync"
    _description = "Refund Import Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        self._import_refund(job.instance_id, payload)

    @api.model
    def _import_refund(self, instance, refund):
        Binding = self.env["shopify.bisync.binding"]
        binding = Binding.get(self.env, instance, "sale.order",
                              external_id=refund.get("order_id"))
        so = binding and binding.resolve()
        if not so:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "refund_no_invoice",
                _("Refund %(rid)s references Shopify order %(oid)s which is "
                  "not imported.", rid=refund.get("id"),
                  oid=refund.get("order_id")),
                reference=str(refund.get("id")))
            return
        ref = _("Shopify refund %s", refund.get("id"))
        existing = self.env["account.move"].search(
            [("move_type", "=", "out_refund"), ("ref", "=", ref),
             ("company_id", "=", instance.company_id.id)], limit=1)
        if existing:
            return  # webhook redelivery: idempotent
        invoice = so.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice"
            and m.state == "posted")[:1]
        if not invoice:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "refund_no_invoice",
                _("Refund %(rid)s for %(order)s: no posted invoice to credit."
                  " Create the invoice, then retry the job.",
                  rid=refund.get("id"), order=so.name),
                reference=so.client_order_ref, sale_order=so)
            so.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=(instance.admin_user_id or self.env.user).id,
                summary=_("Shopify refund waiting for an invoice"),
                note=_("A Shopify refund arrived but %s has no posted "
                       "invoice yet.", so.name))
            return
        line_vals = self._refund_line_vals(instance, so, invoice, refund)
        if not line_vals:
            return
        move = self.env["account.move"].create({
            "move_type": "out_refund",
            "partner_id": invoice.partner_id.id,
            "company_id": instance.company_id.id,
            "currency_id": invoice.currency_id.id,
            "invoice_origin": so.name,
            "ref": ref,
            "reversed_entry_id": invoice.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": line_vals,
        })  # stays DRAFT by design - never auto-post
        so.message_post(body=_(
            "Draft credit note %(move)s created from Shopify refund "
            "%(rid)s - review and post it.",
            move=move.display_name, rid=refund.get("id")))

    @api.model
    def _refund_line_vals(self, instance, so, invoice, refund):
        shopify_mode = instance.tax_policy == "shopify"
        by_shopify_id = {line.shopify_bisync_line_id: line
                         for line in so.order_line
                         if line.shopify_bisync_line_id}
        commands = []
        tax_total = 0.0
        for rli in refund.get("refund_line_items") or []:
            li = rli.get("line_item") or {}
            so_line = by_shopify_id.get(str(rli.get("line_item_id") or ""))
            product = (so_line.product_id if so_line
                       else instance.fallback_product_id)
            qty = float(rli.get("quantity") or 1)
            subtotal = float(rli.get("subtotal") or 0)
            tax_total += float(rli.get("total_tax") or 0)
            vals = {
                "product_id": product.id if product else False,
                "quantity": qty,
                "price_unit": subtotal / qty if qty else subtotal,
                "name": li.get("title") or (product and product.display_name)
                or _("Refunded item"),
            }
            if shopify_mode:
                vals["tax_ids"] = [fields.Command.clear()]
            elif so_line:
                # Mirror the taxes of the invoiced line so the credit note
                # nets out against the invoice.
                invoice_line = invoice.invoice_line_ids.filtered(
                    lambda l: l.product_id == so_line.product_id)[:1]
                if invoice_line:
                    vals["tax_ids"] = [
                        fields.Command.set(invoice_line.tax_ids.ids)]
            commands.append(fields.Command.create(vals))
        if shopify_mode and tax_total:
            commands.append(fields.Command.create({
                "product_id": instance.adjustment_product_id.id,
                "quantity": 1, "price_unit": tax_total,
                "name": _("Shopify refunded taxes"),
                "tax_ids": [fields.Command.clear()],
            }))
        for adjustment in refund.get("order_adjustments") or []:
            amount = -float(adjustment.get("amount") or 0)  # Shopify sends negatives
            if not amount:
                continue
            vals = {
                "product_id": instance.adjustment_product_id.id,
                "quantity": 1, "price_unit": amount,
                "name": (_("Refunded shipping")
                         if adjustment.get("kind") == "shipping_refund"
                         else _("Refund adjustment")),
            }
            if shopify_mode:
                vals["tax_ids"] = [fields.Command.clear()]
            commands.append(fields.Command.create(vals))
        return commands
