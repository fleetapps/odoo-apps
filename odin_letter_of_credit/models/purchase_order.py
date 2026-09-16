# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    odin_lc_id = fields.Many2one(
        "odin.letter.of.credit",
        string="Letter of Credit",
        index="btree_not_null",
        tracking=True,
        copy=False,
        domain="[('direction', '=', 'import'), ('partner_id', '=', partner_id),"
        " ('state', 'in', ('draft', 'applied', 'issued', 'advised'))]",
        help="Settle this order under a documentary credit. Bills created from "
        "it inherit the credit, and with it the payment hold.",
    )

    def _prepare_invoice(self):
        """Carry the credit onto the bill, so the hold is not lost at billing."""
        values = super()._prepare_invoice()
        if self.odin_lc_id:
            values["odin_lc_id"] = self.odin_lc_id.id
        return values
