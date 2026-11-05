from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._log_purchase_in_stock_book()
        return moves

    def _log_purchase_in_stock_book(self):
        """A receipt validated in Purchases, or goods returned to the supplier,
        is on the movement log like everything else: the stock has already
        moved, so the line is posted as it is made. A line the book is posting
        itself is already linked to the order line, and is skipped."""
        moves = self.filtered(lambda m: m.state == "done" and m.purchase_line_id and m.product_id.is_storable
                              and m.quantity and m.picking_type_id.warehouse_id)
        moves = moves.filtered(lambda m: (m.location_id.usage == "supplier" and m.location_dest_id.usage == "internal")
                               or (m.location_id.usage == "internal" and m.location_dest_id.usage == "supplier"))
        if not moves:
            return
        Line = self.env["odin.stock.line"].sudo().with_context(odin_stock_book_posting=True)
        posting = Line.search([("purchase_line_id", "in", moves.purchase_line_id.ids), ("state", "=", "draft")])
        for move in moves.filtered(lambda m: m.purchase_line_id not in posting.purchase_line_id):
            move._make_stock_book_line(Line)

    def _make_stock_book_line(self, Line):
        self.ensure_one()
        order_line = self.purchase_line_id.sudo()
        qty = self.product_uom._compute_quantity(self.quantity, self.product_id.uom_id)
        received = self.location_dest_id.usage == "internal"
        warehouse = self.picking_type_id.warehouse_id
        line = Line.with_company(self.company_id).create({
            "company_id": self.company_id.id, "warehouse_id": warehouse.id,
            "date": fields.Date.context_today(self, self.date), "product_id": self.product_id.id,
            "reason": "received" if received else "correction",
            "qty_in": qty if received else 0.0, "qty_out": 0.0 if received else qty,
            "partner_name": order_line.order_id.partner_id.name,
            "partner_id": order_line.order_id.partner_id.id,
            "unit_cost": order_line._get_stock_move_price_unit(),
            "purchase_line_id": order_line.id, "picking_id": self.picking_id.id,
            "purchase_ref": order_line.order_id.partner_ref or False,
            "note": False if received else self.env._("Returned to supplier"),
            "state": "posted", "posted_by_id": self.env.user.id, "posted_on": fields.Datetime.now(),
            "move_ids": [(6, 0, self.ids)],
        })
        line._assign_ref()
        line.onhand_after = line._available(self.product_id, warehouse)[self.product_id.id]
