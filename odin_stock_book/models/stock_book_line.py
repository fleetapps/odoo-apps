import math
from datetime import datetime, time

import pytz

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
SEQUENCE_CODE = "odin.stock.line.%s"

# A posted line is undone by the line that does the opposite, so the
# history stays: what each reason reverses into.
REVERSE = {"sale": "return", "return": "sale", "spoiled": "correction", "sample": "correction",
           "used": "correction", "correction": "correction"}

# Written by posting and cancelling only: not by the form, an import or a
# call from outside, which would turn a posted line back into a draft, or
# mark a draft posted without its stock moves.
SYSTEM_FIELDS = {"state", "ref", "onhand_after", "posted_by_id", "posted_on", "production_id", "picking_id",
                 "scrap_id", "move_ids", "partner_id"}
# What a posted (or cancelled) line keeps as it was.
LOCKED_FIELDS = {"date", "reason", "product_id", "qty_in", "qty_out", "bom_id", "component_ids", "warehouse_id",
                 "partner_name", "unit_cost", "company_id", "reverses_id"}

STATUS = {
    "need_product": ("Pick the item", "warning"),
    "need_reason": ("Pick a reason", "warning"),
    "need_qty": ("Type how many", "warning"),
    "wrong_way": ("Added or removed?", "warning"),
    "tracked": ("Needs lot numbers", "danger"),
    "need_recipe": ("No recipe", "danger"),
    "need_partner": ("Which customer?", "warning"),
    "future": ("Dated ahead", "warning"),
    "short": ("Short", "danger"),
    "ready": ("Ready to post", "primary"),
    "posted": ("Posted", "success"),
    "cancelled": ("Cancelled", "muted"),
}
NEED_KEYS = {"need_product", "need_reason", "need_qty", "wrong_way", "tracked", "need_recipe", "need_partner",
             "future"}


def _normal_name(name):
    return " ".join((name or "").split()).upper()


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
    reverses_id = fields.Many2one("odin.stock.line", string="Reverses", readonly=True, copy=False, index="btree_not_null",
                                  help="The posted line this one undoes.")

    _ref_unique = models.UniqueIndex("(ref) WHERE ref IS NOT NULL", "Another movement already has this reference.")

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
        if not products or not warehouse:
            return dict.fromkeys(products.ids, 0.0)
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
        """Fill the components from the recipe for the quantity made, the
        way the manufacturing order will: kits are opened up into what they
        contain and recipe lines for other variants are left out, so the
        line and the order it posts agree."""
        for line in self.filtered(lambda l: l.state == "draft").with_context(odin_stock_exploding=True):
            if line.reason not in MAKES or not line.product_id or not line.qty_in:
                line.component_ids = [Command.clear()]
                continue
            bom = line.bom_id if line.bom_id in line._recipes() else line._recipes()[:1]
            line.bom_id = bom
            if not bom:
                line.component_ids = [Command.clear()]
                continue
            factor = line.product_id.uom_id._compute_quantity(line.qty_in, bom.product_uom_id, round=False) \
                / bom.product_qty
            _boms, bom_lines = bom.explode(line.product_id, factor, picking_type=bom.picking_type_id)
            commands = [Command.clear()]
            for seq, (bom_line, data) in enumerate(bom_lines):
                product = bom_line.product_id
                if product.type != "consu":
                    continue
                qty = bom_line.product_uom_id._compute_quantity(data["qty"], product.uom_id, round=False)
                commands.append(Command.create({
                    "sequence": seq, "product_id": product.id,
                    "qty": line._round_component(qty, product.uom_id), "from_recipe": True,
                }))
            line.component_ids = commands

    def _place_qty(self):
        """A reason that only adds (or only removes) takes the quantity in its
        own column: 24 typed in Added for a Sale is 24 removed."""
        moved = self.env["odin.stock.line"]
        for line in self.filtered(lambda l: l.state == "draft"):
            if line.direction == "out" and line.qty_in and not line.qty_out:
                super(StockBookLine, line).write({"qty_out": line.qty_in, "qty_in": 0.0})
                moved |= line
            elif line.direction == "in" and line.qty_out and not line.qty_in:
                super(StockBookLine, line).write({"qty_in": line.qty_out, "qty_out": 0.0})
                moved |= line
        return moved

    def _unlimited(self, product):
        """Never short: things nobody counts (ticked on the product) and
        consumables Odoo does not keep stock of."""
        return product.stock_book_unlimited or not product.is_storable

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
        tracked = (self.product_id | self.component_ids.product_id).filtered(lambda p: p.tracking != "none")
        if tracked:
            return "tracked", *STATUS["tracked"], _(
                "%s is tracked by lot or serial number. Post it from Inventory or Manufacturing, where the "
                "numbers are entered.", ", ".join(tracked.mapped("display_name")))
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
        if self.date > fields.Date.context_today(self):
            return "future", *STATUS["future"], _("Dated %s: it can be posted on the day.",
                                                  self.date.strftime("%d %b %Y"))
        available = available if available is not None else self._available(
            self.product_id | self.component_ids.product_id, self.warehouse_id)
        short = []
        if self.qty_out and float_compare(self.qty_out, available.get(self.product_id.id, 0.0),
                                          precision_digits=4) > 0:
            short.append(self.product_id)
        for comp in self.component_ids:
            if self._unlimited(comp.product_id):
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
    def _check_system_fields(self, vals, creating=False):
        if self.env.context.get("odin_stock_book_posting"):
            return
        # A new line may say it is a draft; nothing else is set by hand.
        touched = {key for key in SYSTEM_FIELDS & vals.keys()
                   if not (creating and key == "state" and vals[key] == "draft")}
        if touched:
            raise UserError(_("Posting, cancelling and references are done by the Stock Book: post or cancel "
                              "the line instead of changing %s.", ", ".join(sorted(touched))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_system_fields(vals, creating=True)
            if vals.get("partner_name"):
                vals["partner_name"] = _normal_name(vals["partner_name"])
        lines = super().create(vals_list)
        lines._place_qty()
        lines._explode()
        return lines

    def write(self, vals):
        self._check_system_fields(vals)
        if vals.get("partner_name"):
            vals["partner_name"] = _normal_name(vals["partner_name"])
        if LOCKED_FIELDS & vals.keys() and self.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        result = super().write(vals)
        if {"reason", "qty_in", "qty_out"} & vals.keys():
            self._place_qty()
        if ({"product_id", "qty_in", "qty_out", "reason", "bom_id"} & vals.keys() and "component_ids" not in vals
                and not self.env.context.get("odin_stock_exploding")):
            self._explode()
        return result

    def unlink(self):
        if self.filtered(lambda l: l.state == "posted"):
            raise UserError(_("Posted lines cannot be deleted. Add a Correction line instead."))
        return super().unlink()

    # ------------------------------------------------------------ posting
    def _when(self):
        """The line's date as a datetime: now for today, noon (the user's
        time) for a back-dated line, so it lands on its day."""
        today = fields.Date.context_today(self)
        if self.date >= today:
            return fields.Datetime.now()
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        return tz.localize(datetime.combine(self.date, time(12, 0))).astimezone(pytz.utc).replace(tzinfo=None)

    def _assign_ref(self):
        """The next number for the reason and the line's year, from Odoo's
        gapless sequence: a row lock, so lines posted together or by two
        people at once never share a number, and a failed post gives its
        number back."""
        code = SEQUENCE_CODE % REASONS[self.reason][3]
        ref = self.env["ir.sequence"].next_by_code(code, sequence_date=self.date)
        if not ref:
            raise UserError(_("The numbering for %(reason)s (%(code)s) is missing. Upgrade the Stock Book module.",
                              reason=REASONS[self.reason][1], code=code))
        self.ref = ref

    def action_post(self, allow_short=False):
        if not self.env.user.has_group("odin_stock_book.group_stock_book_supervisor"):
            raise UserError(_("Only a Stock Book supervisor can post. Ask Operations to post it."))
        today = fields.Date.context_today(self)
        for line in self.filtered(lambda l: l.state == "draft").sorted(lambda l: (l.date, l.id)):
            key, label, _tone, detail = line._status()
            if key != "ready" and not (key == "short" and allow_short):
                raise UserError(_("%(item)s: %(why)s. %(detail)s", item=line.product_id.display_name or _("Line"),
                                  why=label, detail=detail))
            context = {"odin_stock_book_posting": True}
            if line.date < today:
                # Journal entries of a back-dated line go on its day too.
                context["force_period_date"] = line.date
            line = line.with_context(**context)
            line._assign_ref()
            moves = getattr(line, f"_post_{'make' if line.reason in MAKES else line.reason}")()
            when = line._when()
            moves.write({"date": when})
            moves.move_line_ids.write({"date": when})
            if line.date < today:
                line._backdate_documents(when)
            line.write({
                "state": "posted", "posted_by_id": self.env.user.id, "posted_on": fields.Datetime.now(),
                "move_ids": [Command.set(moves.ids)],
                "onhand_after": line._available(line.product_id, line.warehouse_id)[line.product_id.id],
            })
        return True

    def _backdate_documents(self, when):
        """The manufacturing order, transfer or scrap shows the line's day,
        not the day it was posted."""
        if self.production_id:
            # force_date: Odoo's own way to date a done order.
            self.production_id.with_context(force_date=True).write({"date_start": when, "date_finished": when})
        if self.picking_id:
            self.picking_id.date_done = when
        if self.scrap_id:
            self.scrap_id.date_done = when

    def _partner(self):
        name = (self.partner_name or "").strip()
        if not name:
            return self.env["res.partner"]
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        Partner = self.env["res.partner"]
        found = Partner.search(
            [("name", "=ilike", escaped), "|", ("company_id", "=", False), ("company_id", "=", self.company_id.id)],
            order="id", limit=20)
        # The company itself rather than a contact person of the same name.
        partner = found.filtered(lambda p: not p.parent_id)[:1] or found[:1]
        if not partner:
            # A name typed in the book is enough for a contact: supervisors
            # need not be allowed to create contacts in general.
            partner = Partner.sudo().create({"name": name.title(), "company_id": False}).sudo(False)
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
            move._set_quantity_done(move.product_id.uom_id._compute_quantity(qty, move.product_uom))
            move.picked = True
        result = mo.with_context(skip_consumption=True, skip_backorder=True, mo_ids_to_backorder=[],
                                 skip_redirection=True).button_mark_done()
        if mo.state != "done":
            raise UserError(_("Odoo asked a question while posting %(ref)s (%(what)s). Post it from "
                              "Manufacturing instead.", ref=self.ref,
                              what=isinstance(result, dict) and (result.get("name") or result.get("res_model"))
                              or mo.state))
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
            # The cost typed on the line is the receipt's value (a manual
            # value in Odoo 19, which only stock managers may write).
            for move in moves.sudo():
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
        self.filtered(lambda l: l.state == "draft").with_context(odin_stock_book_posting=True).write(
            {"state": "cancelled"})
        return True

    def action_open_document(self):
        self.ensure_one()
        record = self.production_id or self.picking_id or self.scrap_id
        if not record:
            return {"type": "ir.actions.act_window", "res_model": "stock.move", "view_mode": "list,form",
                    "domain": [("id", "in", self.move_ids.ids)], "name": self.ref}
        return {"type": "ir.actions.act_window", "res_model": record._name, "res_id": record.id,
                "views": [[False, "form"]]}

    # ------------------------------------------------------------ copy / reverse
    def _copy_as_draft(self, date):
        """The same movement again, as a new draft on ``date``: a filling
        takes the recipe again."""
        self.ensure_one()
        return self.copy({"date": date, "note": False})

    def _reverse(self):
        """A draft that undoes this posted line: a sale comes back as a
        customer return, a scrap or correction as a correction the other
        way. Fillings and receipts are undone in Manufacturing (unbuild) and
        Inventory (return to the supplier)."""
        self.ensure_one()
        if self.state != "posted" or self.reason not in REVERSE:
            raise UserError(_("Only a posted sale, return, scrap or correction can be reversed here."))
        existing = self.search([("reverses_id", "=", self.id), ("state", "!=", "cancelled")], limit=1)
        if existing:
            raise UserError(_("%(ref)s is already reversed by %(other)s.", ref=self.ref,
                              other=existing.ref or _("a draft line")))
        return self.create({
            "date": fields.Date.context_today(self), "warehouse_id": self.warehouse_id.id,
            "company_id": self.company_id.id, "product_id": self.product_id.id,
            "reason": REVERSE[self.reason], "qty_in": self.qty_out, "qty_out": self.qty_in,
            "partner_name": self.partner_name, "reverses_id": self.id,
            "note": _("Reverses %s", self.ref),
        })


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

    def _check_draft(self, lines):
        if lines.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_draft(self.env["odin.stock.line"].browse({v["line_id"] for v in vals_list if v.get("line_id")}))
        return super().create(vals_list)

    def write(self, vals):
        self._check_draft(self.line_id | self.env["odin.stock.line"].browse(vals.get("line_id")))
        return super().write(vals)

    def unlink(self):
        self._check_draft(self.line_id)
        return super().unlink()
