import math
from datetime import datetime, time

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

# Reason -> (direction, label, what it posts as, reference prefix)
REASONS = {
    "filling": ("in", "Bottle filling", "Manufacturing order (filling)", "FIL"),
    "production": ("in", "Production batch", "Manufacturing order (batch)", "PRD"),
    "received": ("in", "Received", "Receipt from supplier", "IN"),
    "return": ("in", "Customer return", "Return from customer", "RET"),
    "sale": ("out", "Sale", "Delivery to customer", "OUT"),
    "spoiled": ("out", "Spoiled", "Scrap", "SCR"),
    "sample": ("out", "Sample / gift", "Scrap (sample or gift)", "SMP"),
    "used": ("out", "Used in-house", "Scrap (used in-house)", "USE"),
    "correction": ("both", "Correction", "Inventory adjustment", "ADJ"),
}
REASON_SELECTION = [(key, value[1]) for key, value in REASONS.items()]
NEEDS_PARTNER = {"sale", "return"}
MAKES = {"filling", "production"}

STATUS = {
    "need_product": ("Pick the item", "warning"),
    "need_reason": ("Pick a reason", "warning"),
    "need_qty": ("Type how many", "warning"),
    "wrong_way": ("Added or removed?", "warning"),
    "need_recipe": ("No recipe", "danger"),
    "need_partner": ("Which customer?", "warning"),
    "short": ("Short", "danger"),
    "ready": ("Ready to post", "primary"),
    "posted": ("Posted", "success"),
    "cancelled": ("Cancelled", "muted"),
}


class StockBookLine(models.Model):
    """One line of the movement log."""

    _name = "odin.stock.line"
    _description = "Stock Book movement"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"
    _rec_name = "ref"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1))
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    ref = fields.Char(string="Reference", readonly=True, copy=False, index=True)
    reason = fields.Selection(REASON_SELECTION, tracking=True)
    product_id = fields.Many2one("product.product", string="Item", check_company=True, tracking=True,
                                 domain=[("is_storable", "=", True)])
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    qty_in = fields.Float(string="Added", digits="Product Unit", tracking=True)
    qty_out = fields.Float(string="Removed", digits="Product Unit", tracking=True)
    partner_name = fields.Char(string="Customer / supplier", tracking=True)
    partner_id = fields.Many2one("res.partner", readonly=True, copy=False)
    note = fields.Char()
    unit_cost = fields.Float(digits="Product Price", help="Received goods: cost per unit, if known. "
                             "Left empty, the item's current cost is used.")
    bom_id = fields.Many2one("mrp.bom", string="Recipe", check_company=True)
    component_ids = fields.One2many("odin.stock.line.component", "line_id", copy=True)
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
                             default="draft", required=True, readonly=True, copy=False, tracking=True)
    onhand_after = fields.Float(digits="Product Unit", readonly=True, copy=False,
                                help="On hand in the warehouse right after this line was posted.")
    posted_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    posted_on = fields.Datetime(readonly=True, copy=False)
    production_id = fields.Many2one("mrp.production", readonly=True, copy=False)
    picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    scrap_id = fields.Many2one("stock.scrap", readonly=True, copy=False)
    move_ids = fields.Many2many("stock.move", readonly=True, copy=False)

    # ------------------------------------------------------------ basics
    @property
    def direction(self):
        return REASONS.get(self.reason, ("both",))[0]

    @property
    def qty(self):
        return self.qty_in or self.qty_out

    def _location(self):
        return self.warehouse_id.lot_stock_id

    @api.model
    def _available(self, products, warehouse):
        """On hand in the warehouse's stock (and its sub-locations)."""
        quants = self.env["stock.quant"]._read_group(
            [("product_id", "in", products.ids), ("location_id", "child_of", warehouse.lot_stock_id.id)],
            ["product_id"], ["quantity:sum"])
        found = {product.id: qty for product, qty in quants}
        return {p.id: found.get(p.id, 0.0) for p in products}

    def _recipes(self):
        self.ensure_one()
        if not self.product_id:
            return self.env["mrp.bom"]
        return self.env["mrp.bom"].search([
            "|", ("product_id", "=", self.product_id.id),
            "&", ("product_id", "=", False), ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
            ("type", "=", "normal"), "|", ("company_id", "=", False), ("company_id", "=", self.company_id.id),
        ], order="sequence, id")

    @api.model
    def _round_component(self, qty, uom):
        """Whole pieces for things counted in units (cartons, bottles,
        labels); the unit's own rounding for liquids and powders."""
        if uom and uom == self.env.ref("uom.product_uom_unit", raise_if_not_found=False):
            return float(math.ceil(round(qty, 6)))
        return uom.round(qty) if uom else round(qty, 2)

    def _explode(self):
        """Fill the components from the recipe for the quantity made."""
        for line in self.filtered(lambda l: l.state == "draft").with_context(odin_stock_exploding=True):
            if line.reason not in MAKES or not line.product_id or not line.qty_in:
                line.component_ids = [Command.clear()]
                continue
            bom = line.bom_id if line.bom_id in line._recipes() else line._recipes()[:1]
            line.bom_id = bom
            if not bom:
                line.component_ids = [Command.clear()]
                continue
            factor = line.product_id.uom_id._compute_quantity(line.qty_in, bom.product_uom_id) / bom.product_qty
            commands = [Command.clear()]
            for seq, bom_line in enumerate(bom.bom_line_ids):
                qty = bom_line.product_uom_id._compute_quantity(bom_line.product_qty * factor,
                                                                bom_line.product_id.uom_id)
                commands.append(Command.create({
                    "sequence": seq, "product_id": bom_line.product_id.id,
                    "qty": line._round_component(qty, bom_line.product_id.uom_id), "from_recipe": True,
                }))
            line.component_ids = commands

    # ------------------------------------------------------------ status
    def _status(self, available=None):
        """(key, label, tone, detail) for the book. ``available``: on hand by
        product id, as projected by the book for drafts."""
        self.ensure_one()
        if self.state != "draft":
            label, tone = STATUS[self.state]
            return self.state, label, tone, ""
        if not self.product_id:
            return "need_product", *STATUS["need_product"], ""
        if not self.reason:
            return "need_reason", *STATUS["need_reason"], ""
        if float_is_zero(self.qty, precision_digits=4) or (self.qty_in and self.qty_out):
            return "need_qty", *STATUS["need_qty"], _("Type a number in Added or Removed, not both.")
        if (self.direction == "in" and self.qty_out) or (self.direction == "out" and self.qty_in):
            word = _("Added") if self.direction == "in" else _("Removed")
            return "wrong_way", *STATUS["wrong_way"], _("%(reason)s goes in %(column)s.",
                                                         reason=REASONS[self.reason][1], column=word)
        if self.reason in MAKES and not self.bom_id:
            return "need_recipe", *STATUS["need_recipe"], _("%s has no recipe (Bill of Materials).",
                                                            self.product_id.display_name)
        if self.reason in NEEDS_PARTNER and not (self.partner_name or "").strip():
            return "need_partner", *STATUS["need_partner"], ""
        available = available if available is not None else self._available(
            self.product_id | self.component_ids.product_id, self.warehouse_id)
        short = []
        if self.qty_out and float_compare(self.qty_out, available.get(self.product_id.id, 0.0),
                                          precision_digits=4) > 0:
            short.append(self.product_id)
        for comp in self.component_ids:
            if comp.product_id.stock_book_unlimited:
                continue
            if float_compare(comp.qty, available.get(comp.product_id.id, 0.0), precision_digits=4) > 0:
                short.append(comp.product_id)
        if short:
            names = ", ".join(p.name for p in short[:2]) + (f" +{len(short) - 2}" if len(short) > 2 else "")
            return "short", _("Short: %s", names), "danger", _(
                "More than is on hand: %s. Count them, receive the delivery, or change the quantity.",
                ", ".join(p.name for p in short))
        return "ready", *STATUS["ready"], ""

    # ------------------------------------------------------------ create / write
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("partner_name"):
                vals["partner_name"] = " ".join(vals["partner_name"].split()).upper()
        lines = super().create(vals_list)
        lines._explode()
        return lines

    def write(self, vals):
        if vals.get("partner_name"):
            vals["partner_name"] = " ".join(vals["partner_name"].split()).upper()
        locked = {"date", "reason", "product_id", "qty_in", "qty_out", "bom_id", "component_ids", "warehouse_id"}
        if locked & vals.keys() and self.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        result = super().write(vals)
        if ({"product_id", "qty_in", "reason", "bom_id"} & vals.keys() and "component_ids" not in vals
                and not self.env.context.get("odin_stock_exploding")):
            self._explode()
        return result

    def unlink(self):
        if self.filtered(lambda l: l.state == "posted"):
            raise UserError(_("Posted lines cannot be deleted. Add a Correction line instead."))
        return super().unlink()

    # ------------------------------------------------------------ posting
    def _when(self):
        """The line's date as a datetime (noon), so back-dated lines land on their day."""
        today = fields.Date.context_today(self)
        if self.date >= today:
            return fields.Datetime.now()
        return datetime.combine(self.date, time(12, 0))

    def _assign_ref(self):
        prefix = REASONS[self.reason][3]
        year = self.date.year
        self.env.cr.execute(
            "SELECT ref FROM odin_stock_line WHERE ref LIKE %s ORDER BY ref DESC LIMIT 1",
            (f"{prefix}/{year}/%",))
        last = self.env.cr.fetchone()
        seq = int(last[0].rsplit("/", 1)[1]) + 1 if last else 1
        self.ref = f"{prefix}/{year}/{seq:04d}"

    def action_post(self, allow_short=False):
        if not self.env.user.has_group("odin_stock_book.group_stock_book_supervisor"):
            raise UserError(_("Only a Stock Book supervisor can post. Ask Operations to post it."))
        for line in self.filtered(lambda l: l.state == "draft").sorted(lambda l: (l.date, l.id)):
            key, label, _tone, detail = line._status()
            if key != "ready" and not (key == "short" and allow_short):
                raise UserError(_("%(item)s: %(why)s. %(detail)s", item=line.product_id.display_name or _("Line"),
                                  why=label, detail=detail))
            line._assign_ref()
            moves = getattr(line, f"_post_{'make' if line.reason in MAKES else line.reason}")()
            when = line._when()
            moves.write({"date": when})
            moves.move_line_ids.write({"date": when})
            line.write({
                "state": "posted", "posted_by_id": self.env.user.id, "posted_on": fields.Datetime.now(),
                "move_ids": [Command.set(moves.ids)],
                "onhand_after": line._available(line.product_id, line.warehouse_id)[line.product_id.id],
            })
        return True

    def _partner(self):
        name = (self.partner_name or "").strip()
        if not name:
            return self.env["res.partner"]
        partner = self.env["res.partner"].search(
            [("name", "=ilike", name), "|", ("company_id", "=", False), ("company_id", "=", self.company_id.id)],
            limit=1)
        if not partner:
            partner = self.env["res.partner"].create({"name": name.title(), "company_id": False})
        self.partner_id = partner
        return partner

    def _post_make(self):
        """A manufacturing order for the quantity made, consuming exactly the
        components on the line."""
        mo = self.env["mrp.production"].create({
            "product_id": self.product_id.id, "product_qty": self.qty_in,
            "product_uom_id": self.product_id.uom_id.id, "bom_id": self.bom_id.id,
            "origin": self.ref, "picking_type_id": self.warehouse_id.manu_type_id.id,
            "location_src_id": self._location().id, "location_dest_id": self._location().id,
            "company_id": self.company_id.id,
        })
        mo.action_confirm()
        wanted = {}
        for comp in self.component_ids:
            wanted[comp.product_id] = wanted.get(comp.product_id, 0.0) + comp.qty
        extra = [p for p in wanted if p not in mo.move_raw_ids.product_id]
        if extra:
            mo.write({"move_raw_ids": [Command.create({
                "product_id": p.id, "product_uom_qty": wanted[p], "product_uom": p.uom_id.id,
                "location_id": mo.location_src_id.id, "location_dest_id": p.property_stock_production.id,
                "raw_material_production_id": mo.id, "company_id": mo.company_id.id,
            }) for p in extra]})
            mo.move_raw_ids.filtered(lambda m: m.state == "draft")._action_confirm()
        mo.qty_producing = self.qty_in
        mo._set_qty_producing()
        for move in mo.move_raw_ids:
            qty = wanted.get(move.product_id, 0.0)
            # A component on several recipe lines: the first move takes it all.
            wanted[move.product_id] = 0.0
            move._set_quantity_done(qty)
            move.picked = True
        result = mo.with_context(skip_consumption=True, skip_backorder=True, mo_ids_to_backorder=[]).button_mark_done()
        if isinstance(result, dict) and result.get("res_model"):
            raise UserError(_("Odoo asked a question while posting %(ref)s (%(what)s). Post it from "
                              "Manufacturing instead.", ref=self.ref, what=result.get("name") or result["res_model"]))
        self.production_id = mo
        return (mo.move_raw_ids | mo.move_finished_ids).filtered(lambda m: m.state == "done")

    def _post_picking(self, picking_type, src, dest, partner):
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id, "location_id": src.id, "location_dest_id": dest.id,
            "partner_id": partner.id, "origin": self.ref, "company_id": self.company_id.id,
            "move_ids": [Command.create({
                "product_id": self.product_id.id, "product_uom_qty": self.qty,
                "product_uom": self.product_id.uom_id.id, "location_id": src.id, "location_dest_id": dest.id,
                "company_id": self.company_id.id,
            })],
        })
        picking.action_confirm()
        for move in picking.move_ids:
            move._set_quantity_done(move.product_uom_qty)
            move.picked = True
        result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids,
                                      skip_sms=True).button_validate()
        if picking.state != "done":
            raise UserError(_("Odoo could not validate %(ref)s (%(what)s).", ref=self.ref,
                              what=isinstance(result, dict) and result.get("name") or picking.state))
        self.picking_id = picking
        return picking.move_ids

    def _post_sale(self):
        wh = self.warehouse_id
        return self._post_picking(wh.out_type_id, self._location(),
                                  self.env.ref("stock.stock_location_customers"), self._partner())

    def _post_return(self):
        wh = self.warehouse_id
        return self._post_picking(wh.in_type_id, self.env.ref("stock.stock_location_customers"),
                                  self._location(), self._partner())

    def _post_received(self):
        wh = self.warehouse_id
        moves = self._post_picking(wh.in_type_id, self.env.ref("stock.stock_location_suppliers"),
                                   self._location(), self._partner())
        if self.unit_cost:
            for move in moves:
                move.value_manual = self.unit_cost * move.quantity
        return moves

    def _post_scrap(self):
        scrap = self.env["stock.scrap"].create({
            "product_id": self.product_id.id, "scrap_qty": self.qty_out,
            "product_uom_id": self.product_id.uom_id.id, "location_id": self._location().id,
            "origin": self.ref, "company_id": self.company_id.id,
        })
        scrap.do_scrap()
        self.scrap_id = scrap
        return scrap.move_ids

    _post_spoiled = _post_scrap
    _post_sample = _post_scrap
    _post_used = _post_scrap

    def _post_correction(self):
        inventory = self.product_id.with_company(self.company_id).property_stock_inventory
        src, dest = (inventory, self._location()) if self.qty_in else (self._location(), inventory)
        move = self.env["stock.move"].create({
            "product_id": self.product_id.id, "product_uom_qty": self.qty, "product_uom": self.product_id.uom_id.id,
            "location_id": src.id, "location_dest_id": dest.id, "origin": self.ref, "is_inventory": True,
            "company_id": self.company_id.id,
        })
        move._action_confirm()
        move._set_quantity_done(self.qty)
        move.picked = True
        move._action_done()
        return move

    def action_cancel(self):
        self.filtered(lambda l: l.state == "draft").write({"state": "cancelled"})
        return True

    def action_open_document(self):
        self.ensure_one()
        record = self.production_id or self.picking_id or self.scrap_id
        if not record:
            return {"type": "ir.actions.act_window", "res_model": "stock.move", "view_mode": "list,form",
                    "domain": [("id", "in", self.move_ids.ids)], "name": self.ref}
        return {"type": "ir.actions.act_window", "res_model": record._name, "res_id": record.id,
                "views": [[False, "form"]]}


class StockBookComponent(models.Model):
    """A component a filling or production line uses: from the recipe, then
    corrected to what was really used."""

    _name = "odin.stock.line.component"
    _description = "Stock Book component"
    _order = "sequence, id"

    line_id = fields.Many2one("odin.stock.line", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    product_id = fields.Many2one("product.product", required=True, domain=[("is_storable", "=", True)])
    uom_id = fields.Many2one(related="product_id.uom_id")
    qty = fields.Float(digits="Product Unit")
    from_recipe = fields.Boolean(readonly=True)

    def write(self, vals):
        if self.line_id.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        return super().write(vals)
