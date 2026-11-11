import math

from odoo import api, fields, models


class StockPicking(models.Model):
    """Delivery note, export packing list, GRN, goods return and replacement
    notes, transfer form: each printed from the transfer it records."""

    _name = "stock.picking"
    _inherit = ["stock.picking", "odin.document.mixin"]

    doc_is_export = fields.Boolean(
        string="Export", compute="_compute_doc_is_export", store=True,
        help="A delivery to a customer in another country: it has an export packing list.")
    doc_pallets = fields.Integer(string="Pallets")
    doc_cbm = fields.Float(string="Volume (CBM)", digits=(16, 3))
    doc_supplier_ref = fields.Char(string="Supplier's delivery note", help="Printed on the GRN.")
    doc_vehicle = fields.Char(string="Vehicle / driver")
    doc_reason = fields.Text(string="Reason", help="Why goods come back or go back: printed on return and replacement notes.")

    @api.depends("picking_type_code", "partner_id.commercial_partner_id.country_id", "company_id.country_id")
    def _compute_doc_is_export(self):
        for picking in self:
            country = picking.partner_id.commercial_partner_id.country_id
            home = picking.company_id.country_id
            picking.doc_is_export = bool(picking.picking_type_code == "outgoing" and country and home
                                         and country != home)

    # ------------------------------------------------------------ templates
    def _doc_lines(self):
        """One row per product moved: quantity done once it is done, else the
        quantity planned; cartons and weights from the product."""
        rows = []
        for move in self.move_ids.filtered(lambda m: m.state != "cancel"):
            product = move.product_id
            done = move.quantity if (move.state == "done" or move.quantity) else move.product_uom_qty
            qty = move.product_uom._compute_quantity(done, product.uom_id) if move.product_uom else done
            carton = product.doc_carton_qty
            lots = move.move_line_ids.lot_id.mapped("name")
            rows.append({
                "move": move, "product": product, "qty": done,
                "ordered": move.purchase_line_id.product_qty if move.purchase_line_id else move.product_uom_qty,
                "uom": move.product_uom.name or product.uom_id.name, "lots": ", ".join(lots),
                "cartons": math.ceil(qty / carton - 1e-9) if carton else 0,
                "net": qty * product.doc_net_weight, "gross": qty * product.weight,
                "description": move.description_picking or product.display_name,
            })
        return rows

    def _doc_totals(self):
        totals = {"qty": 0.0, "cartons": 0, "net": 0.0, "gross": 0.0, "pallets": 0, "cbm": 0.0}
        for picking in self:
            for row in picking._doc_lines():
                for key in ("qty", "cartons", "net", "gross"):
                    totals[key] += row[key]
            totals["pallets"] += picking.doc_pallets
            totals["cbm"] += picking.doc_cbm
        return totals

    def _doc_invoices(self):
        self.ensure_one()
        return self.sale_id.invoice_ids.filtered(lambda m: m.state == "posted" and m.move_type == "out_invoice")

    def _doc_order(self):
        self.ensure_one()
        return self.sale_id or self.purchase_id


class StockQuant(models.Model):
    """The stock count sheet: system quantity, counted quantity, variance."""

    _name = "stock.quant"
    _inherit = ["stock.quant", "odin.document.mixin"]

    def _doc_sheet_ref(self):
        company = self._doc_company()
        locations = self.location_id
        place = locations[:1].complete_name if len(locations) == 1 else "ALL"
        day = fields.Date.context_today(self)
        return f"{company._doc_short()}/SC/{day:%Y%m%d}/{place}"

    def _doc_count_rows(self):
        rows = []
        for quant in self.sorted(lambda q: (q.location_id.complete_name or "", q.product_id.default_code or "",
                                            q.product_id.name or "")):
            counted = quant.inventory_quantity if quant.inventory_quantity_set else None
            rows.append({
                "quant": quant, "location": quant.location_id.complete_name, "product": quant.product_id,
                "lot": quant.lot_id.name or "", "uom": quant.product_uom_id.name, "system": quant.quantity,
                "counted": counted,
                "variance": (counted - quant.quantity) if counted is not None else None,
            })
        return rows
