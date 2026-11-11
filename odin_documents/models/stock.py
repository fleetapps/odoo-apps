import math

from odoo import _, api, fields, models
from odoo.tools import float_is_zero


class StockMove(models.Model):
    """What each line of a delivery is packed as. Cartons and weights follow
    the product (units per carton, net weight, weight); what is typed over
    them, as packed and weighed, is kept. Typing 0 goes back to the product's
    figure. The pallet is typed."""

    _inherit = "stock.move"

    doc_cartons = fields.Integer(
        string="Cartons", compute="_compute_doc_packing", inverse="_inverse_doc_cartons",
        help="Quantity / the product's units per carton, rounded up. Type over it as packed; 0 for the product's.")
    doc_net_weight = fields.Float(
        string="Net kg", compute="_compute_doc_packing", inverse="_inverse_doc_net", digits="Stock Weight",
        help="Quantity x the product's net weight. Type over it as weighed; 0 for the product's.")
    doc_gross_weight = fields.Float(
        string="Gross kg", compute="_compute_doc_packing", inverse="_inverse_doc_gross", digits="Stock Weight",
        help="Quantity x the product's weight (with its packaging). Type over it as weighed; 0 for the product's.")
    doc_cartons_typed = fields.Integer(copy=False)
    doc_net_typed = fields.Float(digits="Stock Weight", copy=False)
    doc_gross_typed = fields.Float(digits="Stock Weight", copy=False)
    doc_pallet = fields.Char(string="Pallet", help="Which pallet the line is on, e.g. 1, or 1-2.")

    def _doc_qty(self):
        """The quantity the documents print: done once there is one, else planned."""
        self.ensure_one()
        return self.quantity if (self.state == "done" or self.quantity) else self.product_uom_qty

    def _doc_from_product(self):
        """(cartons, net kg, gross kg) from the product's figures."""
        self.ensure_one()
        product = self.product_id
        qty = self._doc_qty()
        if self.product_uom and product:
            qty = self.product_uom._compute_quantity(qty, product.uom_id)
        carton = product.doc_carton_qty
        cartons = math.ceil(qty / carton - 1e-9) if carton and qty > 0 else 0
        return cartons, qty * product.doc_net_weight, qty * product.weight

    @api.depends("product_id.doc_carton_qty", "product_id.doc_net_weight", "product_id.weight", "product_uom",
                 "product_uom_qty", "quantity", "state", "doc_cartons_typed", "doc_net_typed", "doc_gross_typed")
    def _compute_doc_packing(self):
        for move in self:
            cartons, net, gross = move._doc_from_product()
            move.doc_cartons = move.doc_cartons_typed or cartons
            move.doc_net_weight = move.doc_net_typed or net
            move.doc_gross_weight = move.doc_gross_typed or gross

    # A figure is kept as typed only when it differs from the product's: the
    # form sends recomputed figures back on save, and those must keep following
    # the product. The figure written is then dropped from the cache, so the
    # next read shows the product's figure after a 0, not the 0.
    def _inverse_doc_cartons(self):
        for move in self:
            value = max(move.doc_cartons, 0)
            move.doc_cartons_typed = 0 if value == move._doc_from_product()[0] else value
        self.invalidate_recordset(["doc_cartons"])

    def _inverse_doc_net(self):
        for move in self:
            value = max(move.doc_net_weight, 0.0)
            move.doc_net_typed = 0.0 if float_is_zero(value - move._doc_from_product()[1], precision_digits=3) else value
        self.invalidate_recordset(["doc_net_weight"])

    def _inverse_doc_gross(self):
        for move in self:
            value = max(move.doc_gross_weight, 0.0)
            move.doc_gross_typed = 0.0 if float_is_zero(value - move._doc_from_product()[2], precision_digits=3) else value
        self.invalidate_recordset(["doc_gross_weight"])


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
    doc_supplier_ref = fields.Char(string="Supplier's delivery note no.", help="Printed on the GRN.")
    doc_vehicle = fields.Char(string="Vehicle / driver")
    doc_reason = fields.Text(string="Reason for the return",
                             help="Printed on the goods return or replacement note.")
    doc_total_cartons = fields.Integer(string="Cartons", compute="_compute_doc_totals")
    doc_total_net = fields.Float(string="Net weight (kg)", compute="_compute_doc_totals", digits="Stock Weight")
    doc_total_gross = fields.Float(string="Gross weight (kg)", compute="_compute_doc_totals", digits="Stock Weight")
    doc_packing_warning = fields.Text(compute="_compute_doc_totals")

    @api.depends("picking_type_code", "partner_id.commercial_partner_id.country_id", "company_id.country_id")
    def _compute_doc_is_export(self):
        for picking in self:
            country = picking.partner_id.commercial_partner_id.country_id
            home = picking.company_id.country_id
            picking.doc_is_export = bool(picking.picking_type_code == "outgoing" and country and home
                                         and country != home)

    @api.depends("move_ids.doc_cartons", "move_ids.doc_net_weight", "move_ids.doc_gross_weight", "move_ids.state",
                 "move_ids.product_id.doc_carton_qty", "move_ids.product_id.doc_net_weight", "move_ids.product_id.weight")
    def _compute_doc_totals(self):
        for picking in self:
            moves = picking.move_ids.filtered(lambda m: m.state != "cancel")
            picking.doc_total_cartons = sum(moves.mapped("doc_cartons"))
            picking.doc_total_net = sum(moves.mapped("doc_net_weight"))
            picking.doc_total_gross = sum(moves.mapped("doc_gross_weight"))
            products = moves.product_id
            gaps = []
            for label, missing in (
                    (_("No units per carton"), products.filtered(lambda p: not p.doc_carton_qty)),
                    (_("No net weight"), products.filtered(lambda p: not p.doc_net_weight)),
                    (_("No weight"), products.filtered(lambda p: not p.weight))):
                if missing:
                    gaps.append(f"{label}: {', '.join(missing.mapped('display_name'))}")
            picking.doc_packing_warning = "\n".join(gaps) if picking.picking_type_code == "outgoing" else False

    def action_doc_open_products(self):
        """The products of this delivery, to fill in what the packing list needs."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "product.template", "name": _("Products to complete"),
                "domain": [("id", "in", self.move_ids.product_id.product_tmpl_id.ids)],
                "views": [(False, "list"), (False, "form")], "target": "current"}

    # ------------------------------------------------------------ templates
    def _doc_lines(self):
        """One row per product moved, as packed."""
        rows = []
        for move in self.move_ids.filtered(lambda m: m.state != "cancel"):
            product = move.product_id
            rows.append({
                "move": move, "product": product, "qty": move._doc_qty(),
                # The purchase line with sudo: a storekeeper prints the GRN without purchase rights.
                "ordered": move.sudo().purchase_line_id.product_qty or move.product_uom_qty,
                "uom": move.product_uom.name or product.uom_id.name,
                "lots": ", ".join(move.move_line_ids.lot_id.mapped("name")),
                "cartons": move.doc_cartons, "net": move.doc_net_weight, "gross": move.doc_gross_weight,
                "pallet": move.doc_pallet or "", "pack": product._doc_pack_size(),
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

    def _doc_refs(self):
        """The order and invoice numbers the transfer's documents print. Read
        with sudo: a storekeeper prints the delivery note without being able
        to open the sales order or the invoice."""
        self.ensure_one()
        picking = self.sudo()
        source = picking.return_id or picking
        sale = picking.sale_id or source.sale_id
        purchase = picking.purchase_id or source.purchase_id
        invoices = sale.invoice_ids.filtered(lambda m: m.state == "posted" and m.move_type == "out_invoice")
        return {
            "order": sale.name or purchase.name or picking.origin or "",
            "customer_ref": sale.client_order_ref or "",
            "supplier_ref": purchase.partner_ref or "",
            "invoices": ", ".join(invoices.mapped("name")),
        }


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
