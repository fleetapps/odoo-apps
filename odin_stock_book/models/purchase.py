import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        if not self.env.context.get("odin_stock_book_posting"):
            moves._log_in_stock_book()
        return moves

    def _stock_book_crossing(self):
        """Moves that change what a warehouse holds: into or out of its stock.
        A transfer inside a warehouse changes nothing on the book."""
        return self.filtered(
            lambda m: m.state == "done" and m.product_id.is_storable and m.quantity
            and (m.location_id.usage == "internal") != (m.location_dest_id.usage == "internal"))

    def _stock_book_warehouse(self):
        self.ensure_one()
        inside = self.location_dest_id if self.location_dest_id.usage == "internal" else self.location_id
        return inside.warehouse_id

    def _log_in_stock_book(self):
        """Whatever moved stock outside the book is on the movement log like
        everything else: the stock has already moved, so the line is posted as
        it is made. Never in the way of the stock move itself: a line that
        cannot be made is logged, and book_log_missing finds it later."""
        moves = self._stock_book_crossing().filtered(lambda m: m._stock_book_warehouse())
        # What a manufacturing order uses goes on the line of what it makes.
        moves = moves.filtered(lambda m: not m.raw_material_production_id)
        if not moves:
            return
        Line = self.env["odin.stock.line"].sudo().with_context(odin_stock_book_posting=True)
        for move in moves:
            try:
                with self.env.cr.savepoint():
                    move._make_stock_book_line(Line)
            except Exception:
                _logger.exception("Stock Book: could not log move %s", move.id)

    def _stock_book_values(self):
        """How a move outside the book reads on the log."""
        self.ensure_one()
        incoming = self.location_dest_id.usage == "internal"
        outside = self.location_id.usage if incoming else self.location_dest_id.usage
        qty = self.product_uom._compute_quantity(self.quantity, self.product_id.uom_id)
        partner = self.picking_id.partner_id or self.partner_id
        values = {"qty_in": qty if incoming else 0.0, "qty_out": 0.0 if incoming else qty, "note": False}
        order_line = self.purchase_line_id.sudo()
        if self.scrap_id:
            reason = "spoiled"
        elif self.is_inventory:
            reason = "correction"
            values["note"] = self.reference or False
        elif self.production_id and incoming:
            reason = "production"
        elif outside == "customer":
            reason = "return" if incoming else "sale"
        elif outside == "supplier" and incoming:
            reason = "received"
            values["unit_cost"] = order_line._get_stock_move_price_unit() if order_line else self.price_unit
        elif outside == "supplier":
            reason = "correction"
            values["note"] = self.env._("Returned to supplier")
        else:
            reason = "correction"
            values["note"] = self.reference or self.origin or False
        if order_line:
            partner = order_line.order_id.partner_id
            values.update(purchase_line_id=order_line.id, purchase_ref=order_line.order_id.partner_ref or False)
        elif reason != "correction" and self.picking_id and not values["note"]:
            values["note"] = self.picking_id.name
        values.update(reason=reason, partner_id=partner.id or False, partner_name=partner.name or False)
        return values

    def _make_stock_book_line(self, Line):
        self.ensure_one()
        warehouse = self._stock_book_warehouse()
        values = self._stock_book_values()
        production = self.production_id if values["reason"] == "production" else self.env["mrp.production"]
        line = Line.with_company(self.company_id).create({
            **values, "company_id": self.company_id.id, "warehouse_id": warehouse.id,
            "date": fields.Date.context_today(self, self.date), "product_id": self.product_id.id,
            "picking_id": self.picking_id.id, "scrap_id": self.scrap_id.id, "production_id": production.id,
            "bom_id": production.bom_id.id,
            "state": "posted", "posted_by_id": self.env.user.id, "posted_on": fields.Datetime.now(),
            "move_ids": [(6, 0, (self | production.move_raw_ids.filtered(lambda m: m.state == "done")).ids)],
            "component_ids": [(0, 0, {
                "product_id": raw.product_id.id, "from_recipe": True,
                "qty": raw.product_uom._compute_quantity(raw.quantity, raw.product_id.uom_id),
            }) for raw in production.move_raw_ids.filtered(lambda m: m.state == "done" and m.product_id.is_storable)],
        })
        line._assign_ref()
        line.onhand_after = line._available(self.product_id, warehouse)[self.product_id.id]
