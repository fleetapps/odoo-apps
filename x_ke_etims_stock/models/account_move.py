# -*- coding: utf-8 -*-
"""A vendor bill made from an eTIMS supplier invoice confirms it when posted."""

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    ke_etims_purchase_id = fields.Many2one(
        "ke.etims.purchase", string="eTIMS Supplier Invoice", copy=False, index="btree_not_null")

    def _post(self, soft=True):
        posted = super()._post(soft)
        bills = posted.filtered(lambda m: m.ke_etims_purchase_id
                                and m.move_type in ("in_invoice", "in_refund")
                                and m.country_code == "KE")
        Document = self.env["ke.etims.document"].sudo()
        for bill in bills:
            purchase = bill.sudo().ke_etims_purchase_id
            if purchase.state in ("confirmed", "rejected"):
                continue
            # Lines matched since the bill was drafted carry over to the record
            # KRA is told about.
            for line, bill_line in zip(purchase.line_ids.sorted("sequence"),
                                       bill.invoice_line_ids.filtered(
                                           lambda l: l.display_type == "product")):
                if bill_line.product_id and not line.product_id:
                    line.product_id = bill_line.product_id
            purchase.state = "confirming"
            Document._ke_etims_enqueue(purchase, "purchase")
        return posted
